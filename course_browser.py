import hashlib
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error, Frame, Page

from course_data import CourseError


STUDY = "/mycourse/studentstudy"
CATALOG = "/mycourse/studentcourse"
COURSE_HOME = "/mycourse/stu"
SPACE_URL = "https://i.mooc.chaoxing.com/space/index"
COURSE_LINKS = '.learnCourse .course-info a.color1[href*="/visit/stucoursemiddle"]'
QUIZ_BLOCK = ".TiMu:visible"
EXAM_BLOCK = ".questionLi:visible"
OPTIONS = "ul.Zy_ulTop li"
EXAM_OPTIONS = (
    "li[role=radio], li[role=checkbox], "
    "label:has(input[type=radio], input[type=checkbox]):not(li[role=radio] label, li[role=checkbox] label)"
)
SUBMIT = "a, button, input[type=submit]"
SUBMISSION_TIMEOUT = 15
POLL_MS = 250
PAUSE_TIMEOUT = 3
STALL_TIMEOUT = 10
VIDEO_COMPLETED = '.ans-job-finished, [title="任务点已完成"], [aria-label="任务点已完成"]'
RATE_TEXT = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(?:[x×]|倍(?:速)?)\s*", re.I)


class SubmissionCancelled(CourseError):
    pass

CATALOG_DATA = r"""
() => {
    const progress = document.body?.innerText.match(/(?:已完成任务点|任务点进度)\s*[:：]?\s*(\d+)\s*\/\s*(\d+)/);
    const rows = [...document.querySelectorAll('.chapter_item[id^=cur], .posCatalog_select[id^=cur]')];
    return {
        progress: progress ? [Number(progress[1]), Number(progress[2])] : null,
        chapters: rows.map(row => {
            const hints = [row.innerText, row.title, ...[...row.querySelectorAll('[title], [aria-label]')]
                .flatMap(el => [el.title, el.getAttribute('aria-label')])].join(' ');
            const active = row.classList.contains('posCatalog_active');
            const locked = /需完成之前|未解锁|该章节锁定/.test(hints)
                || (row.classList.contains('chapter_item') && !row.hasAttribute('onclick') && !active);
            const pending = hints.match(/(\d+)\s*个待完成任务点|待完成任务点\s*[:：]?\s*(\d+)/);
            const badge = row.querySelector('.knowledgeJobCount, .jobCount, .job-count, .task-count');
            return {
                id: row.id, name: row.querySelector('a')?.textContent.trim() || row.innerText.trim().split('\n')[0],
                active, locked, clickable: row.hasAttribute('onclick') || !!row.querySelector('[onclick]'),
                pending: pending ? Number(pending[1] || pending[2]) : Number(badge?.value || badge?.textContent || 0)
            };
        })
    };
}
"""

QUIZ_DATA = r"""
([blocks, choices]) => ({
    font: [...document.querySelectorAll('style')].map(el => el.textContent)
        .filter(css => /@font-face/.test(css) && /base64,/.test(css)).join('\n'),
    questions: [...document.querySelectorAll(blocks)].filter(block => block.getClientRects().length).map(block => {
        const title = block.querySelector('.Zy_TItle, .mark_name, .question-title');
        const stem = title?.querySelector('.font-cxsecret') || block.querySelector('.font-cxsecret') || title;
        const type = title?.innerText.match(/单选题|多选题|判断题/)?.[0]
            || block.innerText.match(/单选题|多选题|判断题/)?.[0] || '';
        return {
            stem: stem?.innerText.trim() || '',
            type,
            id: block.querySelector('[qid]')?.getAttribute('qid')
                || block.querySelector('input[id^=answer]')?.id.match(/\d+$/)?.[0]
                || block.id.match(/\d+$/)?.[0] || '',
            grade: block.querySelector('.CorrectOrNot .marking_dui') ? 'correct'
                : block.querySelector('.CorrectOrNot .marking_cuo') ? 'wrong' : '',
            options: [...block.querySelectorAll(choices)].map(li => {
                const option = li.querySelector('a.fl.after, .font-cxsecret, .option-text') || li;
                const aria = li.getAttribute('aria-checked') ?? li.querySelector('[aria-checked]')?.getAttribute('aria-checked');
                const input = li.querySelector('input[type=radio], input[type=checkbox]');
                const marker = li.querySelector('.num_option, .num_option_dx');
                return {text: option.innerText.trim(), checked: aria === 'true' ? true
                    : aria === 'false' ? false : input ? input.checked
                    : marker ? marker.matches('.check_answer, .check_answer_dx') : null};
            })
        };
    })
})
"""


def site_path(url):
    parts = urlsplit(url)
    if parts.scheme == "https" and (parts.hostname or "").endswith(".chaoxing.com"):
        return parts.path.rstrip("/")
    return ""


def course_page(url):
    return site_path(url).endswith((STUDY, CATALOG, COURSE_HOME)) or exam_page(url)


def exam_page(url):
    return bool(re.search(r"/(?:exam|exam-ans)(?:/|$)", site_path(url)))


def space_page(url):
    return site_path(url) == "/space/index" and urlsplit(url).hostname == "i.mooc.chaoxing.com"


def course_choices(page):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        for frame in page.frames:
            if not site_path(frame.url).endswith("/visit/interaction"):
                continue
            links = frame.locator(COURSE_LINKS).filter(visible=True)
            if links.count():
                items = links.evaluate_all(
                    "(links) => links.map(a => [a.innerText.trim(), a.href, a.target])"
                )
                return [(links.nth(index), name, href, target)
                        for index, (name, href, target) in enumerate(items)
                        if name and site_path(href).endswith("/visit/stucoursemiddle")]
        page.wait_for_timeout(250)
    raise CourseError("个人空间里没有找到课程，请确认「我学的课」已打开")


def open_course_choice(page, choice):
    link, name, href, target = choice
    if link.inner_text().strip() != name or link.evaluate("a => a.href") != href:
        raise CourseError("课程列表已变化，请重新选择")
    selected = page.context.new_page() if target == "_blank" else page
    try:
        selected.goto(href, wait_until="domcontentloaded", timeout=15000)
        selected.wait_for_url(course_page, wait_until="domcontentloaded", timeout=15000)
    except Error as error:
        if selected != page:
            selected.close()
        raise CourseError("所选课程未进入章节页面，请在 Edge 中查看课程状态") from error
    return selected


@dataclass
class State:
    kind: str
    chapters: list
    frame: Frame | Page | None = None
    progress: list | None = None

    @property
    def chapter(self):
        return next((chapter for chapter in self.chapters if chapter["active"]), None)


class CourseBrowser:
    def __init__(self, page, remove_pause_listener=None):
        self.page = page
        self.remove_pause_listener = remove_pause_listener

    def inspect(self):
        page = self.page
        path = site_path(page.url)
        exam_frames = [frame for frame in page.frames if exam_page(frame.url)]
        exam_forms = [frame for frame in exam_frames if frame.locator(f"{QUIZ_BLOCK}, {EXAM_BLOCK}").count()]
        if len(exam_forms) == 1:
            return State("exam", [], exam_forms[0])
        if len(exam_forms) > 1:
            raise CourseError("考试页存在多个答题框架，无法确认完整页面，请人工核对")
        if exam_page(page.url):
            return State("loading", [])
        if path.endswith((CATALOG, COURSE_HOME)):
            catalog = next((frame for frame in page.frames if site_path(frame.url).endswith(CATALOG)), None)
            if catalog:
                data = catalog.evaluate(CATALOG_DATA)
                if data["chapters"]:
                    return State("catalog", data["chapters"], catalog, data["progress"])
            return State("loading", [])
        if not path.endswith(STUDY):
            return State("unsupported", [])

        data = page.evaluate(CATALOG_DATA)
        chapters, progress = data["chapters"], data["progress"]
        if page.locator(".jobFinishTip:visible").count():
            return State("locked", chapters, progress=progress)
        tab = page.locator("li[id^=dct].active")
        label = tab.first.inner_text() if tab.count() == 1 else ""
        if "章节测验" in label:
            for frame in page.frames:
                route = site_path(frame.url)
                if "/work/" not in route:
                    continue
                if route.endswith("/selectWorkQuestionYiPiYue") and frame.get_by_text(
                    re.compile(r"^\s*已完成\s*$")
                ).count():
                    return State("quiz_done", chapters, frame, progress)
                if frame.locator(QUIZ_BLOCK).count() and (
                    self.submit_button(frame).count() or self.save_button(frame).count()
                    or frame.evaluate("document.readyState") == "complete"
                ):
                    return State("quiz", chapters, frame, progress)
        elif "视频" in label:
            videos = [frame for frame in page.frames
                      if "/ananas/modules/video/" in site_path(frame.url) and frame.locator("video").count() == 1]
            if videos:
                for frame in sorted(videos, key=self.frame_order):
                    if not self.video_completed(frame):
                        return State("video", chapters, frame, progress)
                return State("video_done", chapters, videos[-1], progress)
        return State("loading", chapters, progress=progress)

    @staticmethod
    def frame_order(frame):
        order = []
        while frame.parent_frame:
            order.append(frame.frame_element().evaluate(
                "el => [...el.ownerDocument.querySelectorAll('iframe')].indexOf(el)"
            ))
            frame = frame.parent_frame
        return tuple(reversed(order))

    def wait_for(self, accept, message, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                state = self.inspect()
            except Error as error:
                if self.page.is_closed():
                    raise CourseError("课程标签已关闭") from error
            else:
                if accept(state):
                    return state
                if state.kind == "locked":
                    raise CourseError("当前任务未完成，平台阻止跳转")
                if state.kind == "unsupported":
                    raise CourseError("课程页已跳转到不受支持的页面")
            self.page.wait_for_timeout(POLL_MS)
        raise CourseError(message)

    def ready(self):
        state = self.wait_for(lambda state: state.kind != "loading", "课程页面尚未加载或结构不受支持")
        if state.kind == "unsupported":
            raise CourseError("请打开《美学原理》的章节目录、视频或章节测验")
        return state

    def open_chapter(self, state, chapter):
        source = state.frame if state.kind == "catalog" else self.page
        if not re.fullmatch(r"cur\d+", chapter["id"]) or source is None:
            raise CourseError("无法定位指定章节")
        try:
            source.locator(f'#{chapter["id"]} a').first.click()
        except Error as error:
            raise CourseError("无法点击目录中的待办章节") from error
        return self.wait_for(
            lambda next_state: next_state.kind not in {"loading", "catalog", "unsupported"}
            and next_state.chapter and next_state.chapter["id"] == chapter["id"],
            "点击后未进入指定章节",
        )

    def play(self, state, timeout, on_complete=None):
        if self.remove_pause_listener:
            try:
                self.remove_pause_listener()
            except Error as error:
                raise CourseError("无法检查课程页的失焦暂停监听器") from error
        frame = state.frame
        video = frame.locator("video")
        status = "() => { const v = document.querySelector('video'); return [v.currentTime, v.paused, v.ended, v.error?.code || 0]; }"
        position, paused, ended, error = frame.evaluate(status)
        original_muted = video.evaluate("v => v.muted")
        def moved_on(item):
            return item.kind in {"video_done", "quiz", "quiz_done"} or (
                item.kind == "video" and item.frame != frame
            )

        try:
            retry_rate = False
            video.evaluate("v => { v.muted = true; }")
            self.dismiss_note_tip(frame)
            if paused and not ended and not error:
                buttons = frame.locator(".vjs-big-play-button:visible, .vjs-play-control:visible")
                if not buttons.count():
                    raise CourseError("未找到播放器的播放按钮")
                try:
                    buttons.first.click(timeout=3000)
                except Error as error:
                    raise CourseError("无法点击视频播放按钮，请查看播放器和页面弹窗") from error
            if not ended and not error and not frame.is_detached():
                video.evaluate("v => { v.muted = true; }")
                rate = self.max_playback_rate(frame, video)
                retry_rate = rate is None
                print("静音播放" if retry_rate else f"静音播放，当前倍速 {rate:g}x")
            deadline = time.monotonic() + timeout
            last_movement = time.monotonic()
            paused_since = None
            while time.monotonic() < deadline:
                if frame.is_detached():
                    return self.wait_for(
                        moved_on,
                        "视频页面已变化，当前任务状态无法确认",
                    )
                if self.video_completed(frame):
                    if on_complete:
                        on_complete()
                    return self.wait_for(moved_on, "已完成视频，但无法确认下一项任务")
                current, paused, ended, error = frame.evaluate(status)
                if error:
                    raise CourseError(f"视频播放失败，播放器错误码 {error}")
                if current > position:
                    if retry_rate:
                        video.evaluate("v => { v.muted = true; }")
                        rate = self.max_playback_rate(frame, video)
                        if rate is None:
                            current_rate = video.evaluate("v => v.playbackRate")
                            print(f"播放器未提供可选倍速，保持 {current_rate:g}x")
                        else:
                            print(f"当前倍速 {rate:g}x")
                        retry_rate = False
                    last_movement = time.monotonic()
                if ended:
                    following = self.wait_for(moved_on, "视频已结束，平台尚未确认任务点完成")
                    if on_complete:
                        on_complete()
                    return following
                now = time.monotonic()
                dismissed_tip = self.dismiss_note_tip(frame)
                if paused:
                    if dismissed_tip:
                        video.evaluate("v => { v.play().catch(() => {}); }")
                        paused_since = None
                    elif paused_since is None:
                        paused_since = now
                    elif now - paused_since >= PAUSE_TIMEOUT:
                        raise CourseError(f"播放器已暂停 {PAUSE_TIMEOUT} 秒，请检查播放器提示")
                else:
                    paused_since = None
                if now - last_movement >= STALL_TIMEOUT:
                    raise CourseError(f"视频进度 {STALL_TIMEOUT} 秒未推进，请检查网络或播放器")
                position = current
                self.page.wait_for_timeout(POLL_MS)
            raise CourseError("视频超过本次等待时限")
        finally:
            try:
                if not frame.is_detached() and video.count():
                    video.evaluate(
                        "(v, muted) => { if (!v.paused) v.pause(); v.muted = muted; }",
                        original_muted, timeout=2000,
                    )
            except Error:
                pass

    @staticmethod
    def dismiss_note_tip(frame):
        tip = frame.locator(".writeNote_vid_box:visible").filter(
            has_text="可边看边记，笔记会标记视频时间点"
        )
        button = tip.get_by_text("知道了", exact=True)
        if button.count() == 1:
            try:
                button.click(timeout=1500)
            except Error as error:
                if button.count():
                    raise CourseError("记笔记提示挡住播放器，无法自动关闭") from error
            return True
        return False

    @staticmethod
    def max_playback_rate(frame, video):
        current = video.evaluate("v => v.playbackRate")
        menu = frame.locator(
            ".vjs-playback-rate .vjs-menu-item:visible, "
            ".vjs-playback-rate [role=menuitem]:visible, "
            ".vjs-playback-rate [role=menuitemradio]:visible"
        )
        if not frame.locator(".vjs-playback-rate").count():
            return None

        def open_menu():
            if menu.count():
                return
            video.hover(timeout=1500)
            control = frame.locator(".vjs-playback-rate:visible").first
            if control.count():
                control.hover(timeout=1500)
                if not menu.count():
                    control.click(timeout=1500)

        def option_labels():
            return menu.evaluate_all("""items => items.map(el =>
                el.closest('[disabled], [aria-disabled="true"], .vjs-disabled') ? null :
                (el.querySelector('.vjs-menu-item-text')?.textContent || el.innerText).trim()
            )""")

        try:
            open_menu()
        except Error:
            return None
        rates = {}
        for label in option_labels():
            match = RATE_TEXT.fullmatch(label or "")
            if match:
                rate = float(match[1])
                if 0 < rate < float("inf"):
                    rates[rate] = label
        if not rates:
            return None
        for rate, label in sorted(rates.items(), reverse=True):
            if current >= rate:
                break
            try:
                open_menu()
                labels = option_labels()
                if label not in labels:
                    continue
                menu.nth(labels.index(label)).click(timeout=1500)
                current = video.evaluate("v => v.playbackRate")
            except Error:
                continue
            if abs(current - rate) < 0.01:
                break
        return current

    @staticmethod
    def video_completed(frame):
        return frame.frame_element().evaluate(
            """(element, selector) => {
                const task = element.closest('.ans-attach-ct') || element;
                return task.matches(selector) || !!task.querySelector(selector);
            }""",
            VIDEO_COMPLETED,
        )

    def quiz_tab(self):
        tab = self.page.locator("li[id^=dct]").filter(has_text="章节测验")
        if tab.count() > 1:
            raise CourseError("发现多个测验标签，需要人工选择")
        return tab

    def open_quiz(self):
        try:
            tab = self.quiz_tab()
            if not tab.count():
                raise CourseError("当前章节没有章节测验入口")
            tab.click(timeout=3000)
        except Error as error:
            raise CourseError("无法点击当前章节的测验入口") from error
        return self.wait_for(lambda item: item.kind in {"quiz", "quiz_done"}, "章节测验未加载或仍未解锁")

    def advance(self, state):
        if not state.chapter:
            raise CourseError("无法确认当前章节")
        index = state.chapters.index(state.chapter)
        if index == len(state.chapters) - 1:
            return None
        following = state.chapters[index + 1]
        if not re.fullmatch(r"cur\d+", following["id"]):
            raise CourseError("无法识别下一章节")
        next_button = self.page.locator("#prevNextFocusNext:visible")
        if following["locked"] or not next_button.count():
            raise CourseError("下一章节未解锁或缺少可用入口")
        try:
            next_button.click(timeout=3000)
        except Error as error:
            raise CourseError("无法点击下一节") from error
        return self.wait_for(
            lambda item: item.kind not in {"loading", "unsupported", "catalog"}
            and item.chapter and item.chapter["id"] == following["id"],
            "点击下一节后未进入预期章节",
        )

    @staticmethod
    def submit_button(frame):
        return frame.locator(SUBMIT).filter(has_text=re.compile(r"^\s*提交(?:答案)?\s*$"), visible=True)

    @staticmethod
    def save_button(frame):
        known = frame.locator("#tempsave:visible")
        if known.count():
            return known
        return frame.locator(SUBMIT + ", input[type=button], [role=button]").filter(
            has_text=re.compile(r"^\s*(?:暂时保存|暂存)\s*$"), visible=True
        )

    def confirm_draft(self, state):
        self.save_button(state.frame).click(timeout=3000)
        success = re.compile(r"^\s*(?:暂时保存成功|保存成功|暂存成功|已暂存)[！!。]?\s*$")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for scope in (state.frame, self.page):
                if scope.get_by_text(success).filter(visible=True).count():
                    return
                if scope.get_by_text(re.compile(r"^\s*(?:暂存|保存)失败")).filter(visible=True).count():
                    raise CourseError("平台提示暂存失败")
            self.page.wait_for_timeout(POLL_MS)
        raise CourseError("已点击暂时保存，平台未确认结果，请核对当前测验")

    @staticmethod
    def quiz_selectors(state):
        if state.kind == "exam":
            if not state.frame.locator(QUIZ_BLOCK).count():
                return EXAM_BLOCK, EXAM_OPTIONS
            if state.frame.locator(".questionLi:not(.TiMu):not(:has(.TiMu)):visible").count():
                raise CourseError("考试题目布局混合，无法确认整页题目，请人工核对")
        return QUIZ_BLOCK, OPTIONS

    def quiz_data(self, state):
        blocks, options = self.quiz_selectors(state)
        return state.frame.evaluate(QUIZ_DATA, [blocks.removesuffix(":visible"), options])

    def apply_choices(self, state, plan, questions):
        block_selector, option_selector = self.quiz_selectors(state)
        blocks = state.frame.locator(block_selector)
        for index, targets in enumerate(plan):
            options = questions[index]["options"]
            existing = {i for i, option in enumerate(options) if option["checked"]}
            if any(option["checked"] is None for option in options):
                raise CourseError(f"无法核验第 {index + 1} 题的选中状态")
            if len(targets) == 1 and questions[index]["type"] != "多选题":
                changed = set(targets) - existing
            else:
                changed = set(targets) ^ existing
            for option in sorted(changed):
                blocks.nth(index).locator(option_selector).nth(option).click()

    def confirm_submission(self, state):
        key = self.submission_key(state)
        self.submit_button(state.frame).click()
        deadline = time.monotonic() + SUBMISSION_TIMEOUT
        while time.monotonic() < deadline:
            result = self.inspect()
            if self.submission_key(result) != key:
                raise CourseError("当前课程或章节已变化，请先核对提交结果")
            if result.kind == "quiz_done":
                return result
            popup = self.page.locator("#popcontent:visible")
            if popup.count():
                message = popup.inner_text()
                if "您还有未做完的" in message:
                    self.page.locator("#popno:visible").click()
                    raise SubmissionCancelled("平台提示有未完成的题目，已取消提交")
                if "确认提交" in message:
                    self.page.locator("#popok:visible").click()
                    return self.wait_for(lambda item: item.kind == "quiz_done", "提交后未确认完成，请先核对页面")
                raise CourseError("提交后出现未知提示，请先核对页面")
            self.page.wait_for_timeout(250)
        raise CourseError("提交后未确认完成，请先核对页面")

    def submission_identity(self, state, chapter=None):
        chapter = chapter or state.chapter
        if not chapter:
            return None
        sources = [self.page.url]
        if state.kind == "catalog" and state.frame:
            sources.append(state.frame.url)
        for url in sources:
            values = {key.lower(): value[0] for key, value in parse_qs(urlsplit(url).query).items()}
            keys = [values.get(key) for key in ("courseid", "clazzid", "cpi")]
            if all(keys):
                scope = "/".join(keys)
                return hashlib.sha256(scope.encode()).hexdigest(), hashlib.sha256(
                    (scope + "/" + chapter["id"]).encode()
                ).hexdigest()
        return None

    def submission_key(self, state):
        identity = self.submission_identity(state)
        if not identity:
            raise CourseError("无法确认当前课程、账号和章节标识")
        return identity[1]
