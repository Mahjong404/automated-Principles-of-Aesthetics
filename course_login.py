import getpass
import json
import os
import socket
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error

from course_browser import EXAM_BLOCK, QUIZ_BLOCK, SPACE_URL, course_page, exam_page, space_page, site_path
from course_data import CourseError


def read_account(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    print("无法读取本地账号配置，已忽略该配置")
    return {}


def cdp_endpoints():
    profile_root = Path(os.environ.get("LOCALAPPDATA", ""))
    endpoints = []
    for browser in ("Microsoft/Edge", "Google/Chrome"):
        try:
            port, route, *_ = (profile_root / browser / "User Data/DevToolsActivePort").read_text().splitlines()
            if 1 <= int(port) <= 65535 and route.startswith("/devtools/browser/"):
                endpoints.append((int(port), f"ws://127.0.0.1:{port}{route}"))
        except (OSError, ValueError):
            pass
    for port in (9222, 9223):
        endpoints.extend([(port, f"http://127.0.0.1:{port}"),
                          (port, f"ws://127.0.0.1:{port}/devtools/browser")])
    for port, endpoint in dict.fromkeys(endpoints):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                pass
        except OSError:
            continue
        yield port, endpoint


def reusable_context(playwright):
    connected = set()
    fallback = None
    for port, endpoint in cdp_endpoints():
        if port in connected:
            continue
        try:
            browser = playwright.chromium.connect_over_cdp(
                endpoint, timeout=12000, no_defaults=True, is_local=True
            )
        except Error:
            continue
        connected.add(port)
        contexts = browser.contexts
        if contexts and fallback is None:
            fallback = contexts[0]
        pages = [page for context in contexts for page in context.pages
                 if course_page(page.url) or space_page(page.url)]
        if pages:
            visible = []
            for page in pages:
                try:
                    if page.evaluate("document.visibilityState") == "visible":
                        visible.append(page)
                except Error:
                    continue
            direct = [page for page in pages if course_page(page.url)]
            page = (visible or direct or pages)[-1]
            return page.context, page
    return fallback, None


def course_id(url):
    values = {key.lower(): value[0] for key, value in parse_qs(urlsplit(url).query).items()}
    return values.get("courseid", "")


def space_course_name(space, page):
    target = course_id(page.url)
    if target and space:
        for frame in space.frames:
            if not site_path(frame.url).endswith("/visit/interaction"):
                continue
            cards = frame.locator('a.color1[href*="/visit/stucoursemiddle"]')
            try:
                cards.first.wait_for(state="attached", timeout=2500)
            except Error:
                continue
            links = cards.evaluate_all("links => links.map(a => [a.href, a.innerText.trim()])")
            for href, name in links:
                if course_id(href) == target and name:
                    return name
    name = page.locator("#courseName, [data-course-name], .courseName").filter(visible=True)
    if name.count() == 1:
        text = name.get_attribute("data-course-name") or name.inner_text().strip()
        if text:
            return text
    title = page.title().strip()
    return title if title and title not in {"学生学习页面", "学习通", "章节详情", "考试"} else ""


def passport_page(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {"passport2.chaoxing.com", "passport.chaoxing.com"}


def wait_for_session(page, allow_login, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if space_page(page.url):
            for frame in page.frames:
                if not site_path(frame.url).endswith("/visit/interaction"):
                    continue
                try:
                    if frame.get_by_text("我学的课", exact=True).count() or frame.locator(".learnCourse").count():
                        return "authenticated"
                except Error:
                    continue
        if allow_login and passport_page(page.url) and page.locator(".ipt-tel:visible").count():
            return "login"
        page.wait_for_timeout(250)
    raise CourseError("学习通未进入个人空间，请检查网络、登录信息或在浏览器中完成验证")


def submit_login(page, phone, password):
    if not passport_page(page.url):
        raise CourseError("登录页已变化，请在浏览器中核对")
    page.locator(".ipt-tel:visible").fill(phone)
    page.locator(".ipt-pwd:visible").fill(password)
    page.get_by_role("button", name="登录", exact=True).click()
    wait_for_session(page, allow_login=False)


def prompt_credentials():
    try:
        phone = input("学习通账号：").strip()
        password = getpass.getpass("学习通密码：")
    except EOFError as error:
        raise CourseError("需要在终端输入学习通账号和密码") from error
    if not phone or not password:
        raise CourseError("学习通账号和密码不能为空")
    return phone, password


def authenticated_page(context, reused, account, credentials=None):
    if reused and any(
        exam_page(frame.url) and frame.locator(f"{QUIZ_BLOCK}, {EXAM_BLOCK}").count()
        for frame in reused.frames
    ) and not any(passport_page(frame.url) for frame in reused.frames):
        space = next((page for page in context.pages if space_page(page.url)), None)
        return reused, space_course_name(space, reused)
    space = reused if reused and space_page(reused.url) else context.new_page()
    space.set_default_timeout(5000)
    if not space_page(space.url):
        space.goto(SPACE_URL, wait_until="domcontentloaded", timeout=15000)
    status = wait_for_session(space, allow_login=True)
    if status == "authenticated":
        if reused and reused != space:
            name = space_course_name(space, reused)
            space.close()
            return reused, name
        return space, ""

    if credentials is None:
        phone, password = account.get("phone"), account.get("password")
        if isinstance(phone, str) and phone.strip() and isinstance(password, str) and password:
            try:
                submit_login(space, phone.strip(), password)
            except (CourseError, Error):
                print("本地账号登录未成功，将询问账号和密码")
                space.goto(SPACE_URL, wait_until="domcontentloaded", timeout=15000)
                if wait_for_session(space, allow_login=True) == "authenticated":
                    return space, ""
            else:
                return space, ""
        credentials = prompt_credentials()
    submit_login(space, *credentials)
    return space, ""
