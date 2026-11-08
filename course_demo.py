from contextlib import closing, redirect_stderr, redirect_stdout
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from course_browser import CourseBrowser
from course_data import CourseError, QuestionBank
from course_journal import begin_submission, find_attempt, reserve_attempt
from course_report import RunReport
from main import DEFAULT_QUESTIONS, FONT_DIR, ReviewRequired, decoded_questions, operate, run_course


ORIGIN = "https://mooc1.chaoxing.com"
STUDY = "/mycourse/studentstudy"
QUERY = "?courseId=1&clazzid=2&cpi=3"
CATALOG = "/mycourse/studentcourse"
VIDEO = "/ananas/modules/video/index.html"
QUIZ = "/mooc-ans/work/answer"
REVIEW = "/mooc-ans/work/selectWorkQuestionYiPiYue"
EXAM = "/exam-ans/exam/test/reVersionTestStartNew"


def question_html(bank, unmatched=0, nested=False, grade="", native_exam=False):
    markup = []
    for index, kind in enumerate(("单选题", "多选题", "判断题")):
        row = next(row for rows in bank.questions.values() for row in rows if row["type"] == kind
                   and (not nested or kind != "单选题" or any(
                       option.endswith("美学原理》") for option in row["options"]
                   )))
        stem = f"题库没有收录的演示题 {index + 1}" if index < unmatched else row["question"]
        options = list(reversed(row["options"])) if row["options"] else ["错", "对"]
        if native_exam:
            control = "checkbox" if kind == "多选题" else "radio"
            items = "".join(f'<label><input type="{control}" name="q{index}">{escape(text)}</label>'
                            for text in options)
            markup.append(f'<div class="questionLi"><div class="mark_name">【{kind}】{escape(stem)}</div>{items}</div>')
            continue
        if nested:
            role, marker, checked = ("checkbox", "num_option_dx", "check_answer_dx") if kind == "多选题" else (
                "radio", "num_option", "check_answer"
            )
            items = "".join(
                f'<li onclick="toggle(this)" role="{role}"><label><span class="{marker} {checked if i == 0 else ""}"></span>'
                f'</label><a class="fl after">{escape(text.replace("、美学原理》", "、《美学原理》"))}</a></li>'
                for i, text in enumerate(options)
            )
        else:
            items = "".join(
                f'<li onclick="toggle(this)" aria-checked="{str(i == 0).lower()}"><a class="fl after">{escape(text)}</a></li>'
                for i, text in enumerate(options)
            )
        result = f'<div class="CorrectOrNot"><span class="marking_{grade}"></span></div>' if grade and index == 0 else ""
        markup.append(
            f'<div class="TiMu" data-type="{kind}"><div class="Zy_TItle">'
            f'【{kind}】<span class="font-cxsecret">{escape(stem)}</span></div>'
            f'<ul class="Zy_ulTop">{items}</ul>{result}</div>'
        )
    return "".join(markup)


def study_html(scenario):
    if scenario in {"recovery_done", "recovery_legacy", "recovery_mismatch"}:
        starting = REVIEW
    elif scenario in {"direct", "unmatched", "timeout", "reject", "llm_draft", "duplicate_submit"}:
        starting = QUIZ
    else:
        starting = VIDEO
    modal = '<div class="jobFinishTip">当前章节还有任务点未完成</div>' if scenario == "locked" else ""
    completed = 'aria-label="任务点已完成"' if scenario in {"completed", "multi"} else ""
    more = (f'<iframe id="video2" src="{VIDEO}"></iframe><iframe id="video3" src="{VIDEO}"></iframe>'
            if scenario == "multi" else "")
    pause_script = '<script src="/demo/all-classes.js"></script>' if scenario == "mouseout_background" else ""
    return f"""
<div id="cur101" class="posCatalog_select posCatalog_active"><a>1.1 演示章节</a></div>
<div id="cur102" class="posCatalog_select"><a>1.2 下一个章节</a></div>
<ul><li id="dct1" class="{'active' if starting == VIDEO else ''}">1视频</li>
<li id="dct2" class="{'active' if starting != VIDEO else ''}" onclick="openQuiz()">2章节测验</li></ul>
<iframe id="iframe" src="{starting}" {completed}></iframe>
{more}
<button id="prevNextFocusNext" onclick="advance()">下一节</button>
<div id="popcontent" hidden></div>
<button id="popok" hidden onclick="review()">确认</button>
<button id="popno" hidden onclick="closeDialog()">取消</button>
{modal}
<script>
const iframe = document.querySelector('#iframe');
const videoTab = document.querySelector('#dct1');
const quizTab = document.querySelector('#dct2');
const popup = document.querySelector('#popcontent');
const ok = document.querySelector('#popok');
const cancel = document.querySelector('#popno');
const scenario = {scenario!r};
window.demoSubmissions = 0;
window.demoPlays = 0;
window.demoPlayback = [];
window.demoVideoOrder = [];
window.demoSaves = 0;
window.demoOtherMouseouts = 0;
window.addEventListener('mouseout', () => window.demoOtherMouseouts++);
function openQuiz() {{
    quizTab.classList.add('active');
    videoTab.classList.remove('active');
    iframe.src = '{QUIZ}';
    document.querySelector('#video2')?.remove();
    document.querySelector('#video3')?.remove();
}}
function closeDialog() {{
    popup.hidden = ok.hidden = cancel.hidden = true;
}}
function review() {{
    closeDialog();
    iframe.src = '{REVIEW}';
}}
function demoSubmit() {{
    window.demoSubmissions++;
    if (scenario === 'timeout') return;
    if (scenario === 'reject') {{
        popup.textContent = '您还有未做完的';
        popup.hidden = cancel.hidden = false;
        return;
    }}
    popup.textContent = '确认提交？';
    popup.hidden = ok.hidden = false;
}}
function advance() {{
    document.querySelector('#cur101').classList.remove('posCatalog_active');
    document.querySelector('#cur102').classList.add('posCatalog_active');
    quizTab.classList.remove('active');
    videoTab.classList.add('active');
    iframe.removeAttribute('title');
    iframe.src = '{VIDEO}';
}}
</script>
{pause_script}
"""


def response(path, scenario, bank):
    if path == "/demo/all-classes.js":
        return """window.addEventListener('mouseout', event => {
            if (!event.relatedTarget)
                document.querySelector('#iframe').contentDocument.querySelector('video').pause();
        });"""
    if path == "/space/index":
        return '<iframe src="/mooc2-ans/visit/interaction"></iframe>'
    if path == "/mooc2-ans/visit/interaction":
        return f"""
<div class="learnCourse"><div class="course-info">
<a class="color1" href="{ORIGIN}/visit/stucoursemiddle?class=1" target="_blank">其他课程</a>
</div></div>
<div class="learnCourse"><div class="course-info">
<a class="color1" href="{ORIGIN}/visit/stucoursemiddle?class=2" target="_blank">美学原理</a>
</div></div>
"""
    if path == "/visit/stucoursemiddle":
        return f"<script>location.replace('{CATALOG}')</script>"
    if path == CATALOG:
        return f"""
<p>已完成任务点：{'2/2' if scenario.startswith('recovery_') else '0/2'}</p>
<div id="cur101" class="chapter_item" onclick="location.href='{STUDY}{QUERY}'">
  <a>1.1 演示章节</a><input type="hidden" class="knowledgeJobCount" value="2">
</div>
<div id="cur102" class="chapter_item" title="该章节锁定"><a>1.2 下一个章节</a></div>
"""
    if path == STUDY:
        return study_html(scenario)
    if path == VIDEO:
        complete = 'title="任务点已完成"' if scenario == "completed" else ""
        speed_menu = """
<div class="vjs-playback-rate" onmouseenter="this.querySelector('ul').hidden = false">
  <button>速度</button>
  <ul hidden>
    <li class="vjs-menu-item" onclick="changeRate(1.5)">1.5倍</li>
    <li class="vjs-menu-item" onclick="changeRate(2)">2.0x</li>
    <li class="vjs-menu-item" onclick="changeRate(1)">2.5x</li>
    <li class="vjs-menu-item" aria-disabled="true" onclick="changeRate(3)">3x</li>
  </ul>
</div>
""" if scenario in {"normal", "space_full"} else ""
        delayed_menu = scenario == "space_full"
        if delayed_menu:
            speed_menu = speed_menu.replace('<div class="vjs-playback-rate"', '<div hidden class="vjs-playback-rate"')
        note_tip = """
<div class="writeNote_vid_box">可边看边记，笔记会标记视频时间点
<button onclick="this.parentElement.remove()">知道了</button></div>
""" if scenario == "multi" else ""
        return f"""
<div id="badge" {complete}>视频</div>
<video></video><button class="vjs-big-play-button" onclick="playFixture()">播放</button>
{speed_menu}
{note_tip}
<script>
const video = document.querySelector('video');
let playing = false;
let position = 0;
Object.defineProperties(video, {{
    paused: {{get: () => !playing}},
    ended: {{get: () => position >= 1}},
    currentTime: {{get: () => position}}
}});
video.pause = () => {{
    top.demoPlayback.push([video.playbackRate, video.muted]);
    playing = false;
}};
function changeRate(rate) {{
    video.playbackRate = rate;
    document.querySelector('.vjs-playback-rate ul').hidden = true;
}}
function playFixture() {{
    if (!video.muted) throw new Error('Video must be muted before playback');
    if (document.querySelector('.writeNote_vid_box')) throw new Error('Note tip is still open');
    top.demoPlays++;
    top.demoVideoOrder.push(frameElement.id);
    playing = true;
    if ({str(scenario == "mouseout_background").lower()}) setTimeout(() => {{
        top.document.body.dispatchEvent(new MouseEvent('mouseout', {{bubbles: true}}));
    }}, 75);
    if ({str(scenario == "pause_fault").lower()}) {{
        setTimeout(() => {{ playing = false; }}, 100);
        return;
    }}
    if ({str(delayed_menu).lower()}) setTimeout(() => {{
        position = 0.25;
        document.querySelector('.vjs-playback-rate').hidden = false;
    }}, 250);
    setTimeout(() => {{
        if (!playing) return;
        position = 1;
        frameElement.title = '任务点已完成';
        document.querySelector('#badge').title = '任务点已完成';
    }}, {1400 if delayed_menu else 300});
}}
</script>
"""
    if path == REVIEW:
        grade = {"llm_correct": "dui", "llm_wrong": "cuo", "recovery_done": "dui", "recovery_mismatch": "dui"}.get(scenario, "")
        return "<div>已完成</div>" + question_html(bank, unmatched=int(scenario.startswith(("llm_", "recovery_"))), grade=grade)
    if path in {QUIZ, EXAM}:
        debugger_probe = "<script>debugger;</script>" if scenario == "quiz_debugger" else ""
        if scenario == "nested_submit":
            return question_html(bank, nested=True) + """
<a class="btnSave workBtnIndex" role="button" href="javascript:;"><span id="tempsave">暂时保存</span></a>
<a class="btnSubmit workBtnIndex" role="button" href="javascript:;" onclick="top.demoSubmit()"><span>提交</span></a>
<script>
function toggle(option) {
    const multi = option.getAttribute('role') === 'checkbox';
    const marker = multi ? '.num_option_dx' : '.num_option';
    const checked = multi ? 'check_answer_dx' : 'check_answer';
    const label = option.querySelector(marker);
    if (!multi && !label.classList.contains(checked)) {
        for (const item of option.parentElement.querySelectorAll(marker)) item.classList.remove(checked);
    }
    label.classList.toggle(checked);
}
</script>
"""
        unmatched = 2 if path == EXAM else int(scenario == "unmatched" or scenario.startswith("llm_"))
        exam_setup = "<script>window.demoPlays=0;window.demoSubmissions=0;window.demoSaves=0;window.demoSubmit=()=>window.demoSubmissions++;</script>" if path == EXAM else ""
        duplicate = '<button onclick="top.demoSubmit()">提交</button>' if scenario == "duplicate_submit" else ""
        return exam_setup + debugger_probe + duplicate + question_html(bank, unmatched, native_exam=scenario == "exam_false") + """
<button onclick="top.demoSubmit()">提交</button>
<button id="tempsave" onclick="top.demoSaves++; document.querySelector('#saved').hidden = false;">暂时保存</button>
<div id="saved" hidden>暂存成功</div>
<script>
function toggle(option) {
    const wasChecked = option.getAttribute('aria-checked') === 'true';
    if (option.closest('.TiMu').dataset.type !== '多选题') {
        for (const item of option.parentElement.children) item.setAttribute('aria-checked', 'false');
    }
    option.setAttribute('aria-checked', String(!wasChecked));
}
</script>
"""
    return None


class DemoModel(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = json.loads(payload["messages"][1]["content"])
        self.server.requests.append((self.path, self.headers.get("Authorization"), payload, prompt))
        answers = [{"question": row["question"], "choices": ["A"]} for row in prompt["questions"]]
        content = "```json\n" + json.dumps({"answers": answers}) + "\n```"
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def demo(font_dir):
    scenarios = {
        "normal": ("quiz_done", 1, 1, None),
        "completed": ("quiz_done", 0, 1, None),
        "direct": ("quiz_done", 0, 1, None),
        "unmatched": ("quiz", 0, 0, "匹配不完整"),
        "reject": ("quiz", 0, 1, "未完成的题目"),
        "timeout": ("quiz", 0, 1, "未确认完成"),
        "duplicate_submit": ("quiz", 0, 0, "检测到 2 个提交控件"),
        "recovery_done": ("quiz_done", 0, 0, None),
        "recovery_mismatch": ("quiz_done", 0, 0, None),
        "recovery_legacy": ("quiz_done", 0, 0, None),
        "locked": ("locked", 0, 0, "当前任务"),
        "space_full": ("quiz_done", 2, 2, None),
        "multi": ("quiz_done", 2, 1, None),
        "pause_fault": ("video", 1, 0, "播放器已暂停"),
        "mouseout_background": ("quiz_done", 1, 1, None),
        "quiz_debugger": ("quiz_done", 1, 1, None),
        "nested_submit": ("quiz_done", 1, 1, None),
        "llm_correct": ("quiz_done", 1, 1, None),
        "model_configured": ("quiz_done", 1, 1, None),
        "llm_wrong": ("quiz_done", 1, 1, None),
        "llm_unknown": ("quiz_done", 1, 1, None),
        "llm_draft": ("quiz", 0, 0, None),
        "exam_true": ("exam", 0, 0, None),
        "exam_false": ("exam", 0, 0, None),
    }
    with ThreadingHTTPServer(("127.0.0.1", 0), DemoModel) as model:
        model.requests = []
        server = Thread(target=model.serve_forever, daemon=True)
        server.start()
        try:
            with TemporaryDirectory() as temp, sync_playwright() as playwright:
                edge = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    for scenario, expected in scenarios.items():
                        context = edge.new_context(service_workers="block")
                        try:
                            run_demo_case(context, scenario, *expected, QuestionBank(DEFAULT_QUESTIONS),
                                          font_dir, Path(temp), model)
                        finally:
                            context.close()
                finally:
                    edge.close()
        finally:
            model.shutdown()
            server.join()
    print(f"离线验证通过 {len(scenarios)} 个场景")


def run_demo_case(context, scenario, state, plays, submissions, expected_error, bank, font_dir, directory, model):
    def route_request(route):
        url = urlsplit(route.request.url)
        html = response(url.path, scenario, bank) if url.hostname in {"mooc1.chaoxing.com", "i.mooc.chaoxing.com"} else None
        if html is None:
            route.abort()
        else:
            content_type = "text/javascript" if url.path.endswith(".js") else "text/html"
            route.fulfill(content_type=content_type + "; charset=utf-8", body=html)

    context.route("**/*", route_request)
    page = context.new_page()
    page.set_default_timeout(4000)
    journal = directory / f"{scenario}.sqlite3"
    if scenario == "space_full":
        page.goto("https://i.mooc.chaoxing.com/space/index")
        with patch("builtins.input", return_value="2"), patch("main.JOURNAL", journal):
            operate(page, "", None)
        browser = CourseBrowser(context.pages[-1])
        result = browser.ready()
        assert result.kind == state and result.chapter["id"] == "cur102"
        assert browser.page.evaluate("[window.demoPlays, window.demoSubmissions]") == [plays, submissions]
        assert browser.page.evaluate("window.demoPlayback") == [[2, True], [2, True]]
        print(f"离线场景通过 {scenario}")
        return

    path = EXAM if scenario.startswith("exam_") else CATALOG if scenario in {"normal", "recovery_done"} else STUDY
    page.goto(ORIGIN + path + QUERY)
    browser = CourseBrowser(page)
    if scenario.startswith("recovery_"):
        initial = browser.ready()
        chapter = initial.chapters[0]
        scope, key = browser.submission_identity(initial, chapter)
        if scenario == "recovery_legacy":
            with closing(sqlite3.connect(journal)) as database, database:
                database.execute("CREATE TABLE attempts (key TEXT PRIMARY KEY)")
                database.execute("INSERT INTO attempts VALUES (?)", (key,))
        else:
            review = initial if initial.kind == "quiz_done" else browser.open_chapter(initial, chapter)
            questions = decoded_questions(browser, review, font_dir)
            if initial.kind == "catalog":
                page.goto(ORIGIN + CATALOG + QUERY)
            reserve_attempt(journal, key, scope, chapter["id"])
            begin_submission(journal, key, {
                "scope": "1", "questions": questions, "suggestions": {1: [int(scenario == "recovery_mismatch")]},
            })
    if scenario == "quiz_debugger":
        pauses = []
        new_session = context.new_cdp_session

        def monitor_session(target):
            session = new_session(target)

            def resume_unexpected_pause(event):
                pauses.append(event["reason"])
                session.send("Debugger.resume")

            session.on("Debugger.paused", resume_unexpected_pause)
            return session

        with (patch.object(context, "new_cdp_session", side_effect=monitor_session),
              patch("main.JOURNAL", journal), patch.object(CourseBrowser, "advance", return_value=None)):
            operate(page, "美学原理", None)
        assert not pauses, "测验加载期间出现调试暂停"
    elif scenario == "mouseout_background":
        def repeat_after_reload(active_browser, *args):
            assert active_browser.play(active_browser.ready(), 4).kind == "video_done"
            page.reload(wait_until="load")
            session = context.new_cdp_session(page)
            try:
                for _ in range(10):
                    result = session.send("Runtime.evaluate", {
                        "expression": "getEventListeners(window).mouseout.length",
                        "includeCommandLineAPI": True, "returnByValue": True,
                    })
                    if result["result"].get("value") == 1:
                        break
                    page.wait_for_timeout(100)
                else:
                    raise CourseError("刷新后站点的失焦监听器仍存在，或无关监听器被移除")
            finally:
                session.detach()
            return run_course(active_browser, *args)

        with (patch("course_browser.PAUSE_TIMEOUT", 0.5), patch("main.JOURNAL", directory / "background-first.sqlite3"),
              patch.object(CourseBrowser, "advance", return_value=None)):
            with patch("main.run_course", side_effect=repeat_after_reload):
                operate(page, "美学原理", None)
        assert page.evaluate("window.demoOtherMouseouts") >= 1
    else:
        settings = None
        if scenario.startswith(("llm_", "exam_")) or scenario == "model_configured":
            settings = {
                "base_url": f"http://127.0.0.1:{model.server_port}" + ("" if scenario == "llm_correct" else "/v1"),
                "api_key": "demo-key", "model": "demo-model",
                "auto_commit": scenario not in {"llm_draft", "exam_false"},
            }
        request_count = len(model.requests)
        report = RunReport()
        try:
            with (patch("course_browser.SUBMISSION_TIMEOUT", 1), patch("course_browser.PAUSE_TIMEOUT", 0.5),
                  patch("main.GRADE_TIMEOUT", 0.3), patch.object(browser, "advance", return_value=None),
                  redirect_stdout(output := StringIO())):
                outcome = run_course(browser, bank, font_dir, journal, "美学原理", settings, report)
        except CourseError as error:
            if not expected_error or expected_error not in str(error):
                raise
        else:
            assert expected_error is None
            if scenario in {"llm_wrong", "llm_unknown", "llm_draft", "exam_true", "exam_false", "recovery_mismatch"}:
                assert isinstance(outcome, ReviewRequired)
                assert outcome.failed == (scenario in {"llm_wrong", "llm_unknown", "recovery_mismatch"})
        print(output.getvalue(), end="")
        if scenario == "normal":
            assert "已完成 0/2" in output.getvalue() and "后续有 1 个锁定项" in output.getvalue()
            assert len(report.completed) == 2
        if scenario == "unmatched":
            assert "题库匹配 2/3，1 题待核对" in output.getvalue()
        if scenario == "duplicate_submit":
            with redirect_stderr(receipt := StringIO()):
                report.print_receipt("检测到 2 个提交控件 https://example.test/?token=private", failed=True)
            summary = receipt.getvalue().splitlines()[-1]
            assert "章节=1.1 演示章节 | 阶段=检查测验控件 | 原因=检测到 2 个提交控件" in summary
            assert "private" not in summary and not journal.exists()
        if scenario.startswith("recovery_"):
            assert "平台已确认上次提交" in report.completed[0]
            assert find_attempt(journal, key).phase == ("pending" if scenario == "recovery_mismatch" else "complete")
            verified = QuestionBank(None)
            verified.load_verified(journal, "1")
            assert len(verified.verified) == int(scenario == "recovery_done")
        if scenario == "timeout":
            assert page.evaluate("window.demoSubmissions") == 1
            key = browser.submission_key(browser.ready())
            page.close()
            page = context.new_page()
            page.goto(ORIGIN + STUDY + QUERY)
            browser = CourseBrowser(page)
            with redirect_stdout(StringIO()):
                outcome = run_course(browser, bank, font_dir, journal, "美学原理")
            assert outcome.failed and "尚未显示已提交结果" in outcome.message
            assert find_attempt(journal, key).phase == "pending"
            submissions = 0
        called = settings is not None and scenario != "model_configured"
        assert len(model.requests) == request_count + int(called)
        if called:
            endpoint, authorization, payload, prompt = model.requests[-1]
            expected_endpoint = "/chat/completions" if scenario == "llm_correct" else "/v1/chat/completions"
            assert endpoint == expected_endpoint and authorization == "Bearer demo-key"
            assert payload["model"] == "demo-model" and prompt["course"] == "美学原理"
            assert len(prompt["questions"]) == (2 if path == EXAM else 1)
            if path != EXAM:
                assert prompt["chapter"] == "1.1 演示章节"
        if scenario in {"llm_draft", "exam_true", "exam_false"}:
            assert page.evaluate("window.demoSaves") == 1 and not journal.exists()
        if scenario in {"llm_correct", "llm_wrong", "llm_unknown"}:
            verified = QuestionBank(None)
            verified.load_verified(journal, "1")
            assert len(verified.verified) == int(scenario == "llm_correct")
            if verified.verified:
                entry = json.loads(next(iter(verified.verified.values())))
                reversed_options = list(reversed(entry["options"]))
                question = {"stem": entry["question"], "type": entry["type"],
                            "options": [{"text": option} for option in reversed_options]}
                assert verified.match(question, {}) == (reversed_options.index(entry["selected"][0]),)
    assert browser.ready().kind == state
    assert page.evaluate("[window.demoPlays, window.demoSubmissions]") == [plays, submissions]
    if scenario == "multi":
        assert page.evaluate("window.demoVideoOrder") == ["video2", "video3"]
    print(f"离线场景通过 {scenario}")


if __name__ == "__main__":
    demo(FONT_DIR)
