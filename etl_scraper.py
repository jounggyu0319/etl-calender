"""
SNU eTL 스크래퍼 (Moodle)
- 과제(mod/assign), 퀴즈·시험(mod/quiz) 활동 수집
- `seen_ids`는 호출자(DB)에서 관리
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import date, datetime

logger = logging.getLogger(__name__)
CHROME_PROFILE_DIR = str(pathlib.Path.home() / ".etlcal_chrome_profile")

# 동기화·페이지 대기 (초)
PAGE_LOAD_TIMEOUT = 10
SYNC_MAX_SEC = 180
COURSE_SCAN_MAX_SEC = 15

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.safari.options import Options as SafariOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 실제 서울대 eTL(Moodle) 호스트는 myetl.snu.ac.kr 입니다.
ETL_URL = "https://myetl.snu.ac.kr"
ETL_HOME = "https://myetl.snu.ac.kr/"
# Moodle 직접 로그인(통합로그인 우회 시); 통합로그인은 ETL_HOME → 로그인 링크 권장
MOODLE_LOGIN_URL = "https://myetl.snu.ac.kr/login/index.php"
LOGIN_URL = MOODLE_LOGIN_URL
# NSSO 통합로그인 — self/regist·self/searchId 등으로 잘못 열릴 때 복구용
NSSO_SNU_LOGIN_ENTRY = "https://nsso.snu.ac.kr/sso/usr/snu/login"
# 통합로그인 2차 인증(MFA) 단계
NSSO_MFA_LOGIN_VIEW = "https://nsso.snu.ac.kr/sso/usr/snu/mfa/login/view"
# 로그인 창 연 뒤 사용자가 MFA까지 마칠 때까지 `login_resume_session`에서 대기(초)
LOGIN_SESSION_WAIT_SEC = 600


def _nsso_is_self_service_non_login_url(url: str) -> bool:
    """
    NSSO `…/sso/usr/self/…` 중 아이디 찾기·가입 등 로그인 폼이 아닌 경로.
    https://nsso.snu.ac.kr/sso/usr/self/searchId 등은 True.
    """
    u = (url or "").lower()
    if "/sso/usr/self/" not in u:
        return False
    tail = u.split("/sso/usr/self/", 1)[-1].split("?", 1)[0].split("#", 1)[0].strip("/").lower()
    first = tail.split("/")[0] if tail else ""
    if not first:
        return False
    if first in ("login", "auth") or first.startswith("login"):
        return False
    return True


def _nsso_recover_from_self_service_trap(driver: WebDriver) -> None:
    """아이디 찾기·가입 등 잘못된 self 페이지면 통합로그인 진입 URL로 교체."""
    try:
        u = (driver.current_url or "").lower()
    except Exception:
        return
    if not _nsso_is_self_service_non_login_url(u):
        return
    try:
        driver.get(NSSO_SNU_LOGIN_ENTRY)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except Exception:
        pass


# Moodle 사이트 캘린더(예: 월 보기는 클라이언트에서 #view_name=month … 로만 바뀜)
ETL_CALENDAR_WEB_URL = f"{ETL_URL}/calendar"
SEEN_FILE = "seen_assignments.json"

# Moodle 강의 카드·링크 후보 (각 셀렉터 시도 시 매칭 개수 로그)
COURSE_LINK_SELECTORS = [
    "a[href*='course/view.php']",
    "a.aalink[href*='course/view.php']",
    ".course-info-container a",
    ".coursebox a",
    'h3 a[href*="course"]',
    ".card-title a",
    "[data-courseid] a",
    "[data-region='course-events'] a[href*='course/view.php']",
]

# Moodle 4 대시보드는 JS로 카드가 늦게 붙는 경우가 많아 body 이후 추가 대기
DASHBOARD_COURSE_ANCHOR_WAIT_SEC = 45.0


def _past(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() > deadline


def _debugger_json_http_url(debugger_address: str) -> str:
    """`127.0.0.1:9222` → `http://127.0.0.1:9222/json`."""
    addr = (debugger_address or "").strip()
    if not addr:
        addr = "127.0.0.1:9222"
    if not addr.startswith("http"):
        if "://" in addr:
            hostport = addr.split("://", 1)[-1]
        else:
            hostport = addr
        return f"http://{hostport}/json"
    return addr.rstrip("/") + "/json"


def _print_chrome_debugger_port_status(debugger_address: str) -> None:
    url = _debugger_json_http_url(debugger_address)
    try:
        urllib.request.urlopen(url, timeout=2)
        print("✅ Chrome 디버거 포트 열려있음", flush=True)
    except Exception as e:
        print(f"❌ Chrome 디버거 포트 닫혀있음 ({type(e).__name__}: {e}) — Chrome을 --remote-debugging-port=9222 로 다시 실행하세요.", flush=True)


def _wait_dashboard_course_anchors(driver: WebDriver, max_sec: float) -> int:
    """대시보드에서 course/view 링크가 생길 때까지 폴링(지연 렌더 대응)."""
    deadline = time.monotonic() + max_sec
    best = 0
    sel = "a[href*='course/view.php'], a[href*='Course/view.php']"
    while time.monotonic() < deadline:
        try:
            n = len(driver.find_elements(By.CSS_SELECTOR, sel))
        except Exception:
            n = 0
        best = max(best, n)
        if n > 0:
            return n
        time.sleep(0.35)
    return best


def _scroll_dashboard_for_lazy_load(driver: WebDriver) -> None:
    try:
        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);"
            "document.querySelectorAll('[data-region=myoverview], .dashboard-card, main').forEach("
            "e=>{try{e.scrollTop=e.scrollHeight}catch(_){}});"
        )
    except Exception:
        pass


def _step_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[ETL {ts}] {msg}"
    print(line, flush=True)
    logger.info("%s", msg)


def _page_looks_like_etl_route_missing(driver: WebDriver) -> bool:
    """eTL UI 개편 등으로 `/my/` 등 구 경로가 404(Whoops…)로 바뀐 경우."""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        bl = body.lower()
        u = (driver.current_url or "").lower()
    except Exception:
        return False
    if "course/view.php" in u or "mod/assign" in u or "mod/quiz" in u:
        return False
    if "nothing is here" in bl:
        return True
    if "whoops" in bl and "here" in bl:
        return True
    if "해당 페이지를 찾을 수 없습니다" in body or "페이지를 찾을 수 없음" in body:
        return "whoops" in bl or "nothing is here" in bl or "404" in bl
    return False


def _course_link_display_name(el, href: str | None) -> str:
    name = (el.text or "").strip()
    if name:
        return name
    for attr in ("title", "aria-label"):
        v = (el.get_attribute(attr) or "").strip()
        if v:
            return v
    if href and "id=" in href:
        q = href.split("id=", 1)[-1].split("&")[0].split("#")[0]
        if q.isdigit():
            return f"강의 (코스 id={q})"
    return "강의"


from app.etl_types import CollectResult


def _resolve_browser_token(browser: str) -> str:
    """`system`: OS별로 흔한 기본에 가까운 WebDriver 선택(실제 OS ‘기본 브라우저’와 항상 일치하진 않음)."""
    t = (browser or "chrome").strip().lower()
    if t == "system":
        if sys.platform == "darwin":
            return "safari"
        if sys.platform == "win32":
            return "edge"
        return "chrome"
    return t


def _apply_chromium_stealth(driver: WebDriver) -> None:
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                ),
            },
        )
    except Exception:
        pass


def get_driver(
    headless: bool = True,
    browser: str = "chrome",
    chrome_debugger_address: str | None = None,
) -> WebDriver:
    resolved = _resolve_browser_token(browser)
    dbg = (chrome_debugger_address or os.getenv("ETL_CHROME_DEBUGGER_ADDRESS") or "127.0.0.1:9222").strip()

    if resolved == "safari":
        # Safari WebDriver는 환경마다 헤드리스가 불안정해 강제로 창 모드
        opts = SafariOptions()
        return webdriver.Safari(options=opts)

    if resolved == "firefox":
        opts = FirefoxOptions()
        if headless:
            opts.add_argument("-headless")
        else:
            opts.add_argument("-profile")
            opts.add_argument(CHROME_PROFILE_DIR + "_ff")
        opts.set_preference("intl.accept_languages", "ko-KR, en-US, en")
        opts.set_preference("general.useragent.override", "")
        return webdriver.Firefox(options=opts)

    if resolved == "edge":
        opts = EdgeOptions()
        if headless:
            opts.add_argument("--headless=new")
        else:
            opts.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
            opts.add_experimental_option("detach", True)
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1400,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--lang=ko-KR,en-US,en")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        driver = webdriver.Edge(options=opts)
        _apply_chromium_stealth(driver)
        return driver

    # chrome (기본): headed 이면 기존 Chrome 원격 디버깅에 붙음
    if not headless and resolved == "chrome":
        print(f"디버거 주소: {dbg}", flush=True)
        logger.info("디버거 주소: %s", dbg)
        try:
            _print_chrome_debugger_port_status(dbg)
        except Exception as e:
            print(f"[ETL] 디버거 포트 확인 중 예외: {type(e).__name__}: {e}", flush=True)

        driver: WebDriver | None = None
        t_attach = time.perf_counter()
        try:
            print("[ETL] debuggerAddress 로 Chrome 연결 시도…", flush=True)
            options = ChromeOptions()
            options.add_experimental_option("debuggerAddress", dbg)
            driver = webdriver.Chrome(options=options)
            print(
                f"[ETL] debuggerAddress 연결 성공 ({time.perf_counter() - t_attach:.2f}s)",
                flush=True,
            )
        except Exception as e:
            print(
                f"[ETL] debuggerAddress 연결 실패: {type(e).__name__}: {e}",
                flush=True,
            )
            logger.exception("[ETL] debuggerAddress 연결 실패")
            raise RuntimeError(
                "Chrome을 디버그 모드(원격 디버깅)로 실행해 주세요. "
                "예(macOS): `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--remote-debugging-port=9222` 로 Chrome을 띄운 뒤, 이미 myetl에 로그인·MFA까지 "
                f"마친 상태에서 다시 「🔐 eTL 로그인」을 눌러 주세요. (연결 시도 주소: {dbg})"
            ) from e

        try:
            _apply_chromium_stealth(driver)
        except Exception as e:
            print(f"[ETL] stealth 적용 실패(무시): {type(e).__name__}: {e}", flush=True)

        logger.info("[ETL] Chrome 준비 완료 (%.2fs)", time.perf_counter() - t_attach)
        try:
            for idx, h in enumerate(driver.window_handles):
                driver.switch_to.window(h)
                tab_url = driver.current_url or ""
                tab_title = driver.title or ""
                tab_line = f"[ETL] 탭[{idx}] 현재 URL: {tab_url} | 제목: {tab_title[:100]}"
                print(tab_line, flush=True)
                logger.info("[ETL] 탭[%d] url=%s title=%s", idx, tab_url, tab_title)
            chosen = driver.window_handles[0] if driver.window_handles else None
            for h in driver.window_handles:
                driver.switch_to.window(h)
                u = (driver.current_url or "").lower()
                if "myetl.snu.ac.kr" in u:
                    chosen = h
                    break
            if chosen:
                driver.switch_to.window(chosen)
            cur = (driver.current_url or "").lower()
            print(f"[ETL] 선택 탭 current_url: {driver.current_url}", flush=True)
            logger.info("[ETL] 연결 후 current_url=%s", driver.current_url)
            if "myetl.snu.ac.kr" not in cur:
                logger.info("[ETL] myetl.snu.ac.kr 이 아니어서 https://myetl.snu.ac.kr/ 로 이동")
                driver.get("https://myetl.snu.ac.kr/")
                print(f"[ETL] 이동 후 URL: {driver.current_url}", flush=True)
        except Exception as e:
            print(f"[ETL] 탭 순회/URL 확인 중 오류: {type(e).__name__}: {e}", flush=True)
            logger.exception("[ETL] 탭 순회 실패")
        return driver

    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    else:
        options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
        options.add_experimental_option("detach", True)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=ko-KR,en-US,en")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    _apply_chromium_stealth(driver)
    return driver


def _login_page_is_bare_server_error(driver: WebDriver) -> bool:
    """본문이 'Error'만 있는 등 eTL이 자동화·WAF 등으로 막을 때 흔한 형태."""
    try:
        t = driver.find_element(By.TAG_NAME, "body").text.strip()
    except Exception:
        return False
    if not t:
        return True
    tl = t.lower().rstrip(".")
    short = len(t) <= 64
    if short and tl in ("error", "forbidden", "403", "404", "403 forbidden", "404 not found", "bad request"):
        return True
    if short and tl.startswith("error") and len(t) < 20:
        return True
    return False


def _login_error_visible(driver: WebDriver) -> str:
    for sel in (".loginerrors", ".errorbox", "#loginerrormessage", ".alert-danger", ".alert.alert-danger"):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                t = (el.text or "").strip()
                if t and len(t) > 3:
                    return t[:400]
        except Exception:
            continue
    return ""


def _session_looks_logged_in(driver: WebDriver) -> bool:
    """로그인 완료로 보이는 휴리스틱 (로그아웃 링크, 내 강의 URL 등)."""
    try:
        u = (driver.current_url or "").lower()
        path = u.split("?", 1)[0]
    except Exception:
        return False
    if "logout.php" in u or "login/logout.php" in u:
        return True
    try:
        if driver.find_elements(By.CSS_SELECTOR, "a[href*='logout.php'], a[data-action='logout']"):
            return True
        if driver.find_elements(By.CSS_SELECTOR, ".usermenu, [data-region='user-menu'], .userbutton"):
            return True
    except Exception:
        pass
    if "/my/" in u or "/my/courses" in u:
        return True
    if "/calendar" in path and "login/index.php" not in u:
        return True
    if "login/index.php" in path or path.rstrip("/").endswith("/login"):
        return False
    if "nsso.snu.ac.kr" in u or "sso/usr" in u:
        return False
    return bool(u.startswith("http"))


def _portal_href_is_signup_or_password_reset(href: str) -> bool:
    """로그인이 아닌 가입·찾기 링크. Moodle `…/login/signup.php`는 href에 'login'이 들어가 오인되기 쉬움."""
    h = (href or "").lower()
    if not h:
        return True
    if "signup" in h or "sign-up" in h or "/sign_up" in h:
        return True
    if "forgot" in h and "password" in h:
        return True
    if "lostpassword" in h or "forgot_password" in h:
        return True
    # NSSO 통합계정 «신규 가입» (selector `a[href*='sso/usr']`가 여기까지 매칭되는 경우)
    if "personregist" in h or "person_regist" in h:
        return True
    if "sso/usr/self/" in h and "person" in h:
        return True
    if _nsso_is_self_service_non_login_url(h):
        return True
    return False


def _click_etl_portal_login(driver: WebDriver) -> bool:
    """eTL Moodle 홈(https://myetl.snu.ac.kr/)에서 통합로그인(SSO) 링크 클릭."""
    # `sso/usr`만으로는 personRegist(회원가입)까지 잡히므로, 로그인으로 보이는 NSSO 링크를 우선 수집
    def _rank_nsso_login_href(href: str) -> int:
        h = href.lower()
        if _portal_href_is_signup_or_password_reset(href):
            return -1
        if _nsso_is_self_service_non_login_url(href):
            return -1
        if "nsso.snu.ac.kr" not in h and "sso/usr" not in h and "snu.ac.kr/sso" not in h:
            return -1
        score = 0
        # 학내 통합로그인 MFA 단계(아이디·비번 후 2차 인증) — 회원가입(self/regist)보다 우선
        if "mfa" in h and "login" in h:
            score += 12
        elif "snu/mfa" in h or "/mfa/login" in h:
            score += 10
        for kw, w in (
            ("login", 4),
            ("auth", 3),
            ("saml", 3),
            ("oauth", 2),
            ("oidc", 2),
            ("usr/", 1),  # 일반 SSO 진입 (가입 URL은 위에서 제외됨)
        ):
            if kw in h:
                score += w
        if score == 0 and ("nsso.snu.ac.kr" in h or "snu.ac.kr/sso" in h):
            score = 1  # 경로에 키워드가 없어도 NSSO 호스트면 후보로 둠 (personRegist 등은 위에서 제외)
        return score

    try:
        nsso_anchors = driver.find_elements(
            By.CSS_SELECTOR,
            "a[href*='nsso.snu.ac.kr'], a[href*='sso/usr'], a[href*='snu.ac.kr/sso']",
        )
        ranked: list[tuple[int, object]] = []
        for el in nsso_anchors:
            try:
                href = (el.get_attribute("href") or "").lower()
                if not href or "logout" in href:
                    continue
                r = _rank_nsso_login_href(href)
                if r > 0:
                    ranked.append((r, el))
            except Exception:
                continue
        ranked.sort(key=lambda t: t[0], reverse=True)
        for _r, el in ranked:
            try:
                el.click()
                return True
            except Exception:
                continue
    except Exception:
        pass

    for sel in (
        "header a[href*='login']",
        ".login a",
        "a.loginurl",
    ):
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            try:
                href = (el.get_attribute("href") or "").lower()
                if not href or "logout" in href or _portal_href_is_signup_or_password_reset(href):
                    continue
                if any(x in href for x in ("sso", "login", "nsso", "auth")):
                    el.click()
                    return True
            except Exception:
                continue
    for el in driver.find_elements(By.XPATH, "//a[contains(normalize-space(.),'로그인')]"):
        try:
            href = (el.get_attribute("href") or "").lower()
            if "logout" in href or _portal_href_is_signup_or_password_reset(href):
                continue
            el.click()
            return True
        except Exception:
            continue
    return False


def _first_visible_input(driver: WebDriver, by: By, selector: str):
    """동일 셀렉터 다중 매치 중 실제로 보이는 입력란 (NSSO는 숨김 duplicate 필드가 흔함)."""
    for el in driver.find_elements(by, selector):
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            sz = el.size
            if sz.get("height", 0) < 3 or sz.get("width", 0) < 20:
                continue
            return el
        except Exception:
            continue
    return None


# 통합로그인·Moodle에서 쓰이는 아이디 필드 후보 (순서대로 시도)
_USERNAME_FIELD_SELECTORS: tuple[tuple[By, str], ...] = (
    (By.ID, "username"),
    (By.NAME, "username"),
    (By.CSS_SELECTOR, "input[name='username']"),
    (By.ID, "loginId"),
    (By.NAME, "loginId"),
    (By.ID, "userId"),
    (By.NAME, "userId"),
    (By.CSS_SELECTOR, "input[name='loginId']"),
    (By.CSS_SELECTOR, "input[autocomplete='username']"),
    (By.ID, "j_username"),
    (By.NAME, "j_username"),
    (By.CSS_SELECTOR, "input[name='j_username']"),
    (By.ID, "userLoginName"),
    (By.NAME, "userLoginName"),
    (By.CSS_SELECTOR, "input[placeholder*='아이디']"),
    (By.CSS_SELECTOR, "input[placeholder*='아이디를']"),
    (By.CSS_SELECTOR, "input[placeholder*='학번']"),
    (By.CSS_SELECTOR, "input[type='email'][name]"),
)


def _username_pick_visible(driver: WebDriver):
    for by, sel in _USERNAME_FIELD_SELECTORS:
        el = _first_visible_input(driver, by, sel)
        if el is not None:
            return el
    try:
        pw = _first_visible_input(driver, By.CSS_SELECTOR, "input[type='password']")
        if pw is None:
            return None
        form = pw.find_element(By.XPATH, "./ancestor::form[1]")
        for inp in form.find_elements(By.CSS_SELECTOR, "input"):
            t = (inp.get_attribute("type") or "text").lower()
            if t in ("text", "email", "tel", "") and inp.is_displayed() and inp.is_enabled():
                return inp
    except Exception:
        pass
    return None


def _find_username_browsing_frames(driver: WebDriver):
    """기본 문서 + iframe(1단·2단)에서 아이디 입력란 검색. 찾으면 해당 frame에 포커스 유지."""
    driver.switch_to.default_content()
    el = _username_pick_visible(driver)
    if el is not None:
        return el
    tops = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for ti in range(len(tops)):
        driver.switch_to.default_content()
        tops = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if ti >= len(tops):
            break
        try:
            driver.switch_to.frame(tops[ti])
        except Exception:
            driver.switch_to.default_content()
            continue
        el = _username_pick_visible(driver)
        if el is not None:
            return el
        subs = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for si in range(len(subs)):
            try:
                driver.switch_to.frame(subs[si])
                el = _username_pick_visible(driver)
                if el is not None:
                    return el
            except Exception:
                pass
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                try:
                    driver.switch_to.frame(tops[ti])
                except Exception:
                    pass
        driver.switch_to.default_content()
    driver.switch_to.default_content()
    return None


def _find_username_anywhere(driver: WebDriver, timeout: float = 50):
    """NSSO 폼이 늦게 뜨거나 iframe 안에 있을 때까지 재시도."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        el = _find_username_browsing_frames(driver)
        if el is not None:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            except Exception:
                pass
            return el
        time.sleep(0.35)
    driver.switch_to.default_content()
    return None


_PASSWORD_FIELD_SELECTORS: tuple[tuple[By, str], ...] = (
    (By.ID, "password"),
    (By.NAME, "password"),
    (By.ID, "pwd"),
    (By.NAME, "j_password"),
    (By.CSS_SELECTOR, "input[name='j_password']"),
    (By.CSS_SELECTOR, "input[type='password']"),
)


def _find_password_field(driver: WebDriver):
    for by, sel in _PASSWORD_FIELD_SELECTORS:
        el = _first_visible_input(driver, by, sel)
        if el is not None:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            except Exception:
                pass
            return el
    return None


def _auth_login_url_or_form_hint(driver: WebDriver) -> bool:
    """포털 '로그인' 클릭 후, 너무 느슨한 'login' 문자열 매칭은 제거 (일찍 통과해 폼 전에 필드를 찾는 문제)."""
    u = (driver.current_url or "").lower()
    if "personregist" in u:
        return False
    # NSSO «아이디 찾기·가입» 등 self/* — nsso 호스트만으로 True 금지 (searchId 오인 방지)
    if _nsso_is_self_service_non_login_url(u):
        return False
    if "nsso.snu.ac.kr" in u:
        return True
    if "sso/usr" in u and "personregist" not in u:
        return True
    if "login/index.php" in u:
        return True
    if any(x in u for x in ("/oauth/", "/authorize", "saml2", "saml/login", "openid")):
        return True
    try:
        d = driver
        if (
            d.find_elements(By.ID, "username")
            or d.find_elements(By.ID, "loginId")
            or d.find_elements(By.NAME, "loginId")
            or d.find_elements(By.NAME, "username")
            or d.find_elements(By.CSS_SELECTOR, "input[name='j_username']")
        ):
            return True
    except Exception:
        pass
    return False


def _click_login_submit(driver: WebDriver) -> bool:
    for sel in (
        (By.ID, "loginbtn"),
        (By.ID, "loginSubmit"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "#login input[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit'][value*='로그인']"),
        (By.CSS_SELECTOR, "button.login-btn"),
    ):
        try:
            el = driver.find_element(*sel)
            if el and el.is_displayed():
                el.click()
                return True
        except Exception:
            continue
    return False


def _page_looks_like_mfa(driver: WebDriver) -> bool:
    u = (driver.current_url or "").lower()
    if "mfa" in u or "otp" in u:
        return True
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return False
    for needle in ("2단계", "다단계", "OTP", "인증번호", "보안코드", "MFA"):
        if needle in body:
            return True
    return False


def _login_after_submit_wait_session(
    driver: WebDriver,
    *,
    allow_interactive_mfa: bool,
    mfa_max_sec: int,
    post_submit_wait_sec: int,
) -> tuple[bool, str]:
    """비밀번호 제출 이후 MFA·myetl 세션 대기."""
    if _page_looks_like_mfa(driver):
        if not allow_interactive_mfa:
            return (
                False,
                "2차 인증(MFA)이 필요합니다. `.env`에 ETL_HEADLESS=false 로 두고 동기화하면 "
                "Chrome 창에서 직접 MFA를 완료할 수 있습니다.",
            )
        try:
            WebDriverWait(driver, mfa_max_sec).until(
                lambda d: (
                    "myetl.snu.ac.kr" in (d.current_url or "").lower()
                )
                and "nsso" not in (d.current_url or "").lower()
            )
        except Exception:
            return False, "MFA(2차 인증) 완료 대기 중 타임아웃입니다. 창에서 인증을 마쳤는지 확인해 주세요."

    WebDriverWait(driver, post_submit_wait_sec).until(
        lambda d: _session_looks_logged_in(d)
        or bool(_login_error_visible(d))
        or _page_looks_like_mfa(d)
    )

    if _page_looks_like_mfa(driver) and not allow_interactive_mfa:
        return (
            False,
            "2차 인증(MFA)이 필요합니다. ETL_HEADLESS=false 로 창에서 인증해 주세요.",
        )

    err = _login_error_visible(driver)
    if err:
        return False, err

    if not _session_looks_logged_in(driver):
        return False, "로그인 후에도 세션이 확인되지 않았습니다. MFA 또는 아이디·비밀번호를 확인해 주세요."

    driver.switch_to.default_content()
    return True, ""


def login_resume_session(
    driver: WebDriver,
    *,
    allow_interactive_mfa: bool,
    session_wait_sec: int = LOGIN_SESSION_WAIT_SEC,
) -> tuple[bool, str]:
    """
    아이디·비밀번호는 이미 제출된 상태에서, 사용자가 로그인·MFA를 마칠 때까지 대기한 뒤 세션 검증.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        cur = (driver.current_url or "").lower()
        if "myetl.snu.ac.kr" not in cur:
            print(
                "[ETL] login_resume_session: myetl이 아니어서 대시보드로 이동 후 세션 확인",
                flush=True,
            )
            driver.get("https://myetl.snu.ac.kr/my/")
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        if _session_looks_logged_in(driver):
            msg = f"[ETL] login_resume_session: 이미 로그인된 세션 (대기 생략) url={driver.current_url[:140]!r}"
            print(msg, flush=True)
            logger.info("%s", msg)
            return True, ""
    except Exception as e:
        print(f"[ETL] login_resume_session: 빠른 세션 확인 중 오류(계속 대기 루프): {e}", flush=True)
        logger.warning("[ETL] login_resume_session 빠른 확인 실패: %s", e)
    print("[ETL] login_resume_session: 세션 대기 루프 진입", flush=True)
    return _login_after_submit_wait_session(
        driver,
        allow_interactive_mfa=allow_interactive_mfa,
        mfa_max_sec=session_wait_sec,
        post_submit_wait_sec=session_wait_sec,
    )


def login(
    driver: WebDriver,
    username: str,
    password: str,
    *,
    allow_interactive_mfa: bool = False,
    wait_for_session_after_submit: bool = True,
) -> tuple[bool, str]:
    """
    New eTL 포털 → SNU 통합로그인(NSSO) → (필요 시 MFA) → eTL.
    `allow_interactive_mfa=True`이면 MFA 단계에서 대기(창에서 직접 인증).
    `wait_for_session_after_submit=False`이면 제출 직후 True 반환(브라우저에서 수동으로 이어할 때).
    """
    user = (username or "").strip()
    pwd = password or ""
    if not user:
        return False, "저장된 eTL 아이디가 비어 있습니다."
    if not pwd:
        return False, "저장된 eTL 비밀번호가 비어 있습니다."

    try:
        driver.get(ETL_HOME)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        if _login_page_is_bare_server_error(driver):
            return False, "eTL 메인(https://myetl.snu.ac.kr/)에서 Error 응답이 감지되었습니다."

        try:
            if driver.find_elements(By.CSS_SELECTOR, "a[href*='logout.php'], a[data-action='logout']"):
                driver.switch_to.default_content()
                return True, ""
        except Exception:
            pass

        if not _click_etl_portal_login(driver):
            driver.get(MOODLE_LOGIN_URL)
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            if _login_page_is_bare_server_error(driver):
                return (
                    False,
                    "Moodle 직접 로그인 URL에서 Error가 납니다. "
                    "eTL(https://myetl.snu.ac.kr/)의 통합로그인 링크를 찾지 못했을 수 있습니다.",
                )
            try:
                WebDriverWait(driver, 30).until(lambda d: _auth_login_url_or_form_hint(d))
            except Exception:
                pass
        else:
            try:
                time.sleep(0.4)
                _nsso_recover_from_self_service_trap(driver)
            except Exception:
                pass
            try:
                WebDriverWait(driver, 45).until(lambda d: _auth_login_url_or_form_hint(d))
            except Exception:
                u_bad = (driver.current_url or "").lower()
                if _nsso_is_self_service_non_login_url(u_bad):
                    try:
                        _nsso_recover_from_self_service_trap(driver)
                        WebDriverWait(driver, 30).until(lambda d: _auth_login_url_or_form_hint(d))
                    except Exception:
                        return (
                            False,
                            "NSSO가 아이디 찾기·가입(self/*) 페이지로 열렸습니다. "
                            "통합로그인 진입 URL로 옮겼으나 폼을 확인하지 못했습니다.",
                        )
                else:
                    return False, "포털에서 로그인을 눌렀지만 통합로그인(SSO) 화면으로 넘어가지 않았습니다."

        user_el = _find_username_anywhere(driver, timeout=50)
        if user_el is None:
            return (
                False,
                "통합로그인/Moodle 화면에서 아이디 입력란을 찾지 못했습니다. "
                "NSSO가 iframe 안에 있거나 필드 이름이 다른 경우를 대비해 탐색을 넓혔습니다. "
                "여전히 실패하면 Chrome 창(ETL_HEADLESS=false)에서 개발자도구로 아이디 input의 id/name을 확인해 주세요.",
            )

        user_el.clear()
        user_el.send_keys(user)

        pw_el = _find_password_field(driver)
        if pw_el is None:
            return False, "비밀번호 입력란을 찾지 못했습니다."

        pw_el.clear()
        pw_el.send_keys(pwd)

        if not _click_login_submit(driver):
            return False, "로그인(제출) 버튼을 찾지 못했습니다."

        if not wait_for_session_after_submit:
            driver.switch_to.default_content()
            return True, ""

        return _login_after_submit_wait_session(
            driver,
            allow_interactive_mfa=allow_interactive_mfa,
            mfa_max_sec=180,
            post_submit_wait_sec=60,
        )
    except Exception as e:
        err = _login_error_visible(driver)
        if err:
            return False, err
        try:
            tail = (driver.current_url or "")[:160]
        except Exception:
            tail = ""
        return False, f"로그인 중 오류({type(e).__name__}). URL: {tail}"


def _normalize_course_url(href: str | None) -> str | None:
    if not href or "course/view.php" not in href:
        return None
    return href.split("#")[0].strip()


def _course_index_urls() -> list[str]:
    """강의 목록: myetl 대시보드·홈 후 보조 경로."""
    my = "https://myetl.snu.ac.kr"
    return [
        f"{my}/my/",
        f"{my}/",
        f"{my}/my/courses.php",
        f"{my}/my/index.php",
        f"{my}/course/index.php",
    ]


def _primary_etl_base(_driver: WebDriver) -> str:
    """캘린더 등 절대 URL 조합용 베이스(항상 myetl)."""
    return "https://myetl.snu.ac.kr"


def _log_course_page_html_debug(driver: WebDriver, page_url: str) -> None:
    """강의 목록이 비었을 때 DOM 구조 확인용(로그)."""
    try:
        el = driver.find_element(By.CSS_SELECTOR, "#region-main, #content, main, .mydashboard")
    except Exception:
        try:
            el = driver.find_element(By.TAG_NAME, "body")
        except Exception:
            return
    try:
        html = el.get_attribute("outerHTML") or ""
    except Exception:
        return
    logger.info("[ETL] 강의 페이지 HTML 디버그 URL=%s (총 %d자, 8000자씩)", page_url, len(html))
    for i in range(0, min(len(html), 24000), 8000):
        logger.info("[ETL] HTML chunk %d:\n%s", i // 8000, html[i : i + 8000])


def _save_debug_html(driver: WebDriver) -> None:
    """강의를 전혀 못 찾았을 때 page_source를 파일로 저장."""
    path = pathlib.Path.cwd() / "debug_html.txt"
    try:
        path.write_text(driver.page_source or "", encoding="utf-8")
        logger.warning("[ETL] 강의 0건 — HTML 저장: %s", path.resolve())
    except Exception as e:
        logger.error("[ETL] debug_html.txt 저장 실패: %s", e)


def get_courses(driver: WebDriver, deadline: float | None = None) -> list[dict]:
    t0 = time.perf_counter()
    logger.info("[ETL] get_courses 시작")
    by_url: dict[str, str] = {}
    for page_url in _course_index_urls():
        if deadline is not None and time.monotonic() > deadline:
            logger.warning("[ETL] get_courses: 전체 동기화 시간 제한으로 URL 순회 중단")
            break
        t_page = time.perf_counter()
        logger.info("[ETL] get_courses 페이지 로드 시작 %s", page_url)
        try:
            driver.get(page_url)
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            try:
                WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "a[href*='course/view.php'], [data-region='myoverview'], "
                            ".dashboard-card, .coursebox, .coursename, .course-info-container, "
                            ".card-title, [data-courseid]",
                        )
                    )
                )
            except Exception:
                pass
            if _page_looks_like_etl_route_missing(driver):
                logger.info("[ETL] get_courses 404/누락 페이지로 스킵 %s", page_url)
                continue
            if "myetl.snu.ac.kr" in page_url and "calendar" not in page_url:
                _scroll_dashboard_for_lazy_load(driver)
                if deadline is not None:
                    remain = deadline - time.monotonic()
                    wait_sec = max(5.0, min(DASHBOARD_COURSE_ANCHOR_WAIT_SEC, remain))
                else:
                    wait_sec = DASHBOARD_COURSE_ANCHOR_WAIT_SEC
                n_anchor = _wait_dashboard_course_anchors(driver, wait_sec)
                print(f"[ETL] 대시보드 course/view 앵커 대기 후 개수: {n_anchor}", flush=True)
                logger.info("[ETL] 대시보드 course/view 앵커 대기 후 개수: %d", n_anchor)
            if "calendar" in page_url:
                try:
                    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                        EC.presence_of_element_located(
                            (
                                By.CSS_SELECTOR,
                                "a[href*='mod/assign'], a[href*='mod/quiz'], a[href*='course/view.php'], [data-region='month-container']",
                            )
                        )
                    )
                except Exception:
                    time.sleep(1.0)
            n_before = len(by_url)
            for sel in COURSE_LINK_SELECTORS:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    n_match = len(els)
                    added_sel = 0
                    for el in els:
                        href = _normalize_course_url(el.get_attribute("href"))
                        if not href or href in by_url:
                            continue
                        name = _course_link_display_name(el, href)
                        by_url[href] = name
                        added_sel += 1
                        print(f"[ETL] 강의 발견: {name}", flush=True)
                        logger.info("[ETL] 강의 발견: %s", name)
                    line = (
                        f"[ETL] 셀렉터 {sel!r} → DOM {n_match}개, 신규 course/view {added_sel}개 "
                        f"(누적 강의 {len(by_url)})"
                    )
                    print(line, flush=True)
                    logger.info(
                        "[ETL] 셀렉터 %r → DOM %d개, 신규 course/view %d개 (누적 강의 %d)",
                        sel,
                        n_match,
                        added_sel,
                        len(by_url),
                    )
                except Exception as ex:
                    logger.warning("[ETL] 셀렉터 %r 오류: %s", sel, ex)
            logger.info(
                "[ETL] get_courses 페이지 완료 %s (%.2fs) 누적 강의 %d",
                page_url,
                time.perf_counter() - t_page,
                len(by_url),
            )
            if len(by_url) == n_before:
                _log_course_page_html_debug(driver, page_url)
        except Exception as e:
            logger.warning("[ETL] get_courses 페이지 실패 %s: %s", page_url, e)
            continue
    logger.info("[ETL] get_courses 종료 (%.2fs) 총 강의 %d", time.perf_counter() - t0, len(by_url))
    if not by_url:
        _save_debug_html(driver)
    return [{"name": n, "url": u} for u, n in by_url.items()]


def _append_assignments_from_calendar_view(
    driver: WebDriver, out: CollectResult, *, sync_deadline: float | None = None
) -> int:
    """
    월간 캘린더 화면에서 `mod/assign`·`mod/quiz` 링크를 직접 수집합니다.
    코스 목록(`course/view.php`)이 DOM에 없어도 그리드에 보이는 과제·퀴즈를 잡기 위함입니다.
    """
    if _past(sync_deadline):
        return 0
    today = date.today().isoformat()
    base = _primary_etl_base(driver)
    try:
        driver.get(f"{base}/calendar#view_name=month&view_start={today}")
        WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        if _page_looks_like_etl_route_missing(driver):
            return 0
    except Exception:
        return 0
    try:
        WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a[href*='mod/assign'], a[href*='mod/quiz'], [data-region='calendar']")
            )
        )
    except Exception:
        time.sleep(1.0)
    for _ in range(4):
        try:
            driver.execute_script(
                "document.querySelectorAll('[data-region=month-container], .maincalendar, .calendarwrapper')"
                ".forEach(e=>{try{e.scrollTop=e.scrollHeight}catch(_){}});"
            )
        except Exception:
            pass
        time.sleep(0.45)

    by_key: dict[str, tuple[str, str, str]] = {}
    for sel in (
        "a[href*='mod/assign/view.php']",
        "a[href*='mod/quiz/view.php']",
        ".maincalendar a[href*='mod/assign']",
        ".maincalendar a[href*='mod/quiz']",
        "[data-region='calendar'] a[href*='mod/assign']",
        "[data-region='calendar'] a[href*='mod/quiz']",
        ".calendarwrapper a[href*='mod/assign']",
        ".calendarwrapper a[href*='mod/quiz']",
        "td.day a[href*='mod/assign']",
        "td.day a[href*='mod/quiz']",
        "a[data-action='event-name-click'][href*='mod/assign']",
        "a[data-action='event-name-click'][href*='mod/quiz']",
    ):
        try:
            for link in driver.find_elements(By.CSS_SELECTOR, sel):
                href_raw = link.get_attribute("href") or ""
                h = href_raw.split("#")[0]
                if "view.php" not in h or "id=" not in h.lower():
                    continue
                hl = h.lower()
                if "mod/assign" in hl:
                    typ = "assign"
                elif "mod/quiz" in hl:
                    typ = "quiz"
                else:
                    continue
                key = _dedupe_key(h)
                if not key or key in by_key:
                    continue
                title = (link.text or link.get_attribute("aria-label") or link.get_attribute("title") or "").strip()
                if not title:
                    title = "과제" if typ == "assign" else "퀴즈"
                by_key[key] = (title[:500], typ, key)
        except Exception:
            continue

    for key, (title, typ, url) in by_key.items():
        if typ == "assign":
            out.assign_links_found += 1
        else:
            out.quiz_links_found += 1
        if key in out.updated_seen:
            continue
        item: dict = {
            "id": key,
            "title": title,
            "subject": "eTL 캘린더",
            "url": url,
            "activity_type": typ,
        }
        item["deadline"] = fetch_deadline(driver, url, typ, deadline=sync_deadline)
        out.new_items.append(item)
        out.updated_seen.add(key)
    return len(by_key)


def _dedupe_key(url: str) -> str:
    return url.split("#")[0].strip()


def discover_activities_on_course_page(
    driver: WebDriver, course: dict, *, deadline: float | None = None
) -> list[dict]:
    """강의 메인 페이지에서 assign/quiz 링크만 수집 (마감 없음)."""
    if _past(deadline):
        return []
    driver.get(course["url"])
    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    found: dict[str, dict] = {}
    try:
        for href_key, typ in (("mod/assign", "assign"), ("mod/quiz", "quiz")):
            for link in driver.find_elements(By.CSS_SELECTOR, f"a[href*='{href_key}']"):
                url = link.get_attribute("href")
                title = (link.text or "").strip()
                if not url:
                    continue
                if not title:
                    title = url.split("?")[0].rsplit("/", 1)[-1] or typ
                key = _dedupe_key(url)
                if key not in found:
                    found[key] = {
                        "id": key,
                        "title": title,
                        "subject": course["name"],
                        "url": key,
                        "activity_type": typ,
                    }
    except Exception:
        pass
    return list(found.values())


def _body_text(driver: WebDriver) -> str:
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return ""


def extract_deadline_snippet(raw: str | None) -> str | None:
    """페이지 텍스트에서 마감 후보 한 덩어리 추출 (한국어 날짜·ISO)."""
    if not raw:
        return None
    text = raw.replace("\n", " ")
    # Moodle 한국어: "2026년 5월 15일 오후 11:59" 근처
    m = re.search(
        r"(\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*(?:오전|오후)\s*\d{1,2}:\d{2})",
        text,
    )
    if m:
        return m.group(1)
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)", text)
    if m:
        return m.group(1)
    return None


def get_deadline_assign(driver: WebDriver, url: str, *, deadline: float | None = None) -> str | None:
    if _past(deadline):
        return None
    driver.get(url)
    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
    )
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".submissionstatustable .cell.c1")
        t = el.text.strip()
        if t:
            return t
    except Exception:
        pass
    return extract_deadline_snippet(_body_text(driver))


def get_deadline_quiz(driver: WebDriver, url: str, *, deadline: float | None = None) -> str | None:
    if _past(deadline):
        return None
    driver.get(url)
    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
    )
    for sel in (".quizinfo", ".generalbox", "#region-main", ".activity-information"):
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            hit = extract_deadline_snippet(el.text)
            if hit:
                return hit
        except Exception:
            continue
    return extract_deadline_snippet(_body_text(driver))


def fetch_deadline(
    driver: WebDriver, url: str, activity_type: str, *, deadline: float | None = None
) -> str | None:
    if activity_type == "quiz":
        return get_deadline_quiz(driver, url, deadline=deadline)
    if activity_type in ("announcement_midterm", "announcement_final", "forum_notice"):
        return None
    return get_deadline_assign(driver, url, deadline=deadline)


# --- 공지/포럼·페이지 텍스트에서 중간·기말(한·영) 키워드 감지 ---
# mid term / midterm / MT exam 등 영문 변형 포함 (과도한 단일 "MT"는 제외)
_MIDTERM_KW = re.compile(
    r"(?:"
    r"중간\s*고사|중간고사|중간\s*시험|중간시험|중간\s*평가|"
    r"mid\s*terms?|mid\s*-?\s*terms?|midterm|mid\s+term|"
    r"\bMT\s*exam\b|M\.T\.?\s*exam|"
    r"mid-?\s*semester\s*exam|mid-?\s*semester\s*test"
    r")",
    re.I,
)
_FINAL_KW = re.compile(
    r"(?:"
    r"기말\s*고사|기말고사|기말\s*시험|기말시험|기말\s*평가|"
    r"final\s*exam|final\s*examination|final\s*test|"
    r"\bfinals?\b(?!\s*(?:grade|score|project|report|paper))|"
    r"end-?\s*of-?\s*semester\s*exam|end-?\s*of-?\s*term\s*exam|"
    r"comprehensive\s*final"
    r")",
    re.I,
)


def _split_text_for_scan(text: str, max_chunk: int = 480) -> list[str]:
    if not text or not text.strip():
        return []
    normalized = re.sub(r"[ \t\r\f]+", " ", text)
    parts = re.split(r"(?:\n\s*){2,}", normalized)
    chunks: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_chunk:
            chunks.append(p)
            continue
        for i in range(0, len(p), max_chunk):
            chunks.append(p[i : i + max_chunk].strip())
    return [c for c in chunks if c]


def _exam_kinds_for_chunk(chunk: str) -> list[str]:
    kinds: list[str] = []
    if _MIDTERM_KW.search(chunk):
        kinds.append("announcement_midterm")
    if _FINAL_KW.search(chunk):
        kinds.append("announcement_final")
    return kinds


def _stable_announcement_id(course_url: str, activity_type: str, snippet: str) -> str:
    raw = f"{activity_type}|{course_url}|{snippet[:240]}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"etl:ann:{h[:48]}"


def _short_title(snippet: str, max_len: int = 90) -> str:
    one = re.sub(r"\s+", " ", snippet).strip()
    if len(one) <= max_len:
        return one
    return one[: max_len - 1] + "…"


def extract_exam_announcement_items(text: str, course: dict, source_url: str) -> list[dict]:
    """텍스트 블록에서 중간/기말 관련 구절을 캘린더용 항목으로 변환."""
    items: list[dict] = []
    for chunk in _split_text_for_scan(text):
        for typ in _exam_kinds_for_chunk(chunk):
            items.append(
                {
                    "id": _stable_announcement_id(course["url"], typ, chunk),
                    "title": f"공지: {_short_title(chunk)}",
                    "subject": course["name"],
                    "url": source_url,
                    "activity_type": typ,
                    "deadline": chunk,
                }
            )
    return items


def collect_announcement_text_sources(
    driver: WebDriver,
    course: dict,
    *,
    max_forums: int = 4,
    max_pages: int = 2,
    deadline: float | None = None,
) -> list[tuple[str, str]]:
    """
    강의 홈 + 포럼(공지) + 페이지 활동 본문을 (출처 URL, 텍스트)로 수집.
    """
    if _past(deadline):
        return []
    driver.get(course["url"])
    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    sources: list[tuple[str, str]] = [(_dedupe_key(course["url"]), _body_text(driver))]

    forum_urls: list[str] = []
    for link in driver.find_elements(By.CSS_SELECTOR, "a[href*='mod/forum']"):
        href = link.get_attribute("href") or ""
        if "view.php" not in href or "discuss.php" in href:
            continue
        u = _dedupe_key(href)
        if u not in forum_urls:
            forum_urls.append(u)

    for fu in forum_urls[:max_forums]:
        if _past(deadline):
            break
        try:
            driver.get(fu)
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            blobs: list[str] = []
            for sel in (".forumpost .message", ".forumpost .content", ".posting", "#region-main"):
                try:
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        t = (el.text or "").strip()
                        if len(t) > 40:
                            blobs.append(t)
                except Exception:
                    continue
            blob = "\n\n".join(blobs) if blobs else _body_text(driver)
            sources.append((_dedupe_key(fu), blob))
        except Exception:
            continue

    if _past(deadline):
        return sources
    driver.get(course["url"])
    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    page_urls: list[str] = []
    for link in driver.find_elements(By.CSS_SELECTOR, "a[href*='mod/page']"):
        href = link.get_attribute("href") or ""
        if "view.php" not in href:
            continue
        u = _dedupe_key(href)
        if u not in page_urls:
            page_urls.append(u)
    for pu in page_urls[:max_pages]:
        if _past(deadline):
            break
        try:
            driver.get(pu)
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            sources.append((_dedupe_key(pu), _body_text(driver)))
        except Exception:
            continue

    return sources


_FORUM_TITLE_KW_KO = (
    "중간고사",
    "기말고사",
    "시험",
    "퀴즈",
    "과제",
    "마감",
    "제출",
    "공지",
)
_FORUM_TITLE_KW_EN = (
    "midterm",
    "final",
    "exam",
    "quiz",
    "assignment",
    "deadline",
    "due",
    "submission",
    "notice",
    "announcement",
    "test",
)


def _forum_notice_title_matches(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    tl = t.casefold()
    for k in _FORUM_TITLE_KW_KO:
        if k in t:
            return True
    for k in _FORUM_TITLE_KW_EN:
        if k in tl:
            return True
    return False


def _stable_forum_notice_id(course_url: str, discuss_url: str) -> str:
    raw = f"forum_notice|{course_url}|{discuss_url}"
    return "etl:forum:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def _discuss_page_folded_text(driver: WebDriver) -> str:
    blocks: list[str] = []
    for sel in (
        ".forumpost.firstpost .content",
        ".forumpost.firstpost .message",
        ".forumpost .content",
        ".forumpost .message",
        "[data-region='post-content']",
    ):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                tx = (el.text or "").strip()
                if len(tx) > 15:
                    blocks.append(tx)
        except Exception:
            continue
    if blocks:
        return "\n".join(blocks[:5])
    return _body_text(driver)


def discover_forum_notice_items(
    driver: WebDriver, course: dict, *, deadline: float | None = None
) -> list[dict]:
    """mod/forum 토론 목록에서 키워드가 들어간 글을 찾아 캘린더용 항목으로 만듭니다."""
    logger.info("[ETL] 포럼 공지 스캔 — 강의: %s", (course.get("name") or "")[:80])
    if _past(deadline):
        return []
    driver.get(course["url"])
    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    forum_urls: list[str] = []
    for link in driver.find_elements(By.CSS_SELECTOR, "a[href*='mod/forum']"):
        href = link.get_attribute("href") or ""
        if "view.php" not in href or "discuss.php" in href:
            continue
        u = _dedupe_key(href)
        if u not in forum_urls:
            forum_urls.append(u)
    items: list[dict] = []
    for fu in forum_urls[:6]:
        if _past(deadline):
            break
        try:
            logger.info("[ETL] 포럼 목록 — %s", fu[:140])
            driver.get(fu)
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception as e:
            logger.warning("[ETL] 포럼 열기 실패: %s", e)
            continue
        seen_h: set[str] = set()
        thread_links: list[tuple[str, str]] = []
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='discuss.php']"):
            href = a.get_attribute("href") or ""
            if "discuss.php" not in href:
                continue
            h = _dedupe_key(href)
            if not h or h in seen_h:
                continue
            seen_h.add(h)
            title = (a.text or a.get_attribute("title") or "").strip()
            if len(title) < 2:
                continue
            thread_links.append((h, title))
        logger.info("[ETL] 포럼 토론 링크 %d개 (최대 40개까지 검사)", len(thread_links))
        for discuss_url, title in thread_links[:40]:
            if _past(deadline):
                break
            if not _forum_notice_title_matches(title):
                continue
            try:
                logger.info("[ETL] 키워드 매칭 글 — %s", title[:120])
                driver.get(discuss_url)
                WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except Exception as e:
                logger.warning("[ETL] 토론 페이지 실패: %s", e)
                continue
            body = _discuss_page_folded_text(driver)
            blob = f"{title}\n{body}"[:8000]
            aid = _stable_forum_notice_id(course["url"], discuss_url)
            items.append(
                {
                    "id": aid,
                    "title": f"공지: {title[:420]}",
                    "subject": course["name"],
                    "url": discuss_url,
                    "activity_type": "forum_notice",
                    "deadline": blob,
                    "description_extra": body[:3500],
                }
            )
    logger.info("[ETL] 포럼 공지 후보 %d건 (강의 %s)", len(items), (course.get("name") or "")[:40])
    return items


def _collect_assignments_loop(
    driver: WebDriver,
    out: CollectResult,
    *,
    sync_deadline: float | None = None,
    progress_cb: Callable[[dict], None] | None = None,
) -> None:
    """로그인된 driver로 강의·과제·퀴즈·공지 수집. `out`을 갱신합니다."""
    if sync_deadline is None:
        sync_deadline = time.monotonic() + SYNC_MAX_SEC

    def _prog(payload: dict) -> None:
        if progress_cb:
            progress_cb(payload)

    logger.info("[ETL] collect 시작 — URL: %s", driver.current_url)
    t0 = time.perf_counter()
    _step_log("단계: 강의 목록 수집 시작")
    _prog({"phase": "courses", "course_index": 0, "course_total": 0, "course_name": ""})
    courses = get_courses(driver, deadline=sync_deadline)
    _step_log(f"단계: 강의 목록 수집 완료 ({time.perf_counter() - t0:.2f}s) — {len(courses)}개")
    logger.info("[ETL] 강의 수: %d", len(courses))
    if len(courses) == 0:
        logger.warning("[ETL] 강의 0개 — URL: %s / 제목: %s", driver.current_url, driver.title)
    out.courses_found = len(courses)

    n_courses = len(courses)
    for idx, course in enumerate(courses, start=1):
        if time.monotonic() > sync_deadline:
            note = "동기화 전체 시간 상한(3분)에 도달해, 그때까지 찾은 항목만 반영합니다."
            logger.warning("[ETL] %s", note)
            out.collect_failed_note = note
            break
        course_deadline = min(sync_deadline, time.monotonic() + COURSE_SCAN_MAX_SEC)
        cname = (course.get("name") or "")[:120]
        _prog(
            {
                "phase": "course_scan",
                "course_index": idx,
                "course_total": n_courses,
                "course_name": cname,
            }
        )
        _step_log(f"단계: 강의 {idx}/{n_courses} 스캔 시작 — {cname[:60]}")
        logger.info("[ETL] 강의 처리 시작 — %s", (course.get("name") or "")[:80])

        if not _past(course_deadline):
            ann_sources = collect_announcement_text_sources(
                driver, course, deadline=course_deadline
            )
            for src_url, blob in ann_sources:
                if _past(course_deadline):
                    break
                for item in extract_exam_announcement_items(blob, course, src_url):
                    out.announcement_keyword_hits += 1
                    aid = item["id"]
                    if aid in out.updated_seen:
                        continue
                    out.new_items.append(item)
                    out.updated_seen.add(aid)

        if not _past(course_deadline):
            for item in discover_forum_notice_items(driver, course, deadline=course_deadline):
                if _past(course_deadline):
                    break
                out.announcement_keyword_hits += 1
                aid = item["id"]
                if aid in out.updated_seen:
                    continue
                out.new_items.append(item)
                out.updated_seen.add(aid)

        if not _past(course_deadline):
            acts = discover_activities_on_course_page(driver, course, deadline=course_deadline)
            for a in acts:
                if _past(course_deadline):
                    break
                if a["activity_type"] == "assign":
                    out.assign_links_found += 1
                elif a["activity_type"] == "quiz":
                    out.quiz_links_found += 1

                aid = a["id"]
                if aid in out.updated_seen:
                    continue
                a = {
                    **a,
                    "deadline": fetch_deadline(
                        driver, a["url"], a["activity_type"], deadline=course_deadline
                    ),
                }
                out.new_items.append(a)
                out.updated_seen.add(aid)

        _step_log(f"단계: 강의 {idx}/{n_courses} 스캔 완료 ({time.perf_counter() - t0:.2f}s 경과)")

    if time.monotonic() > sync_deadline and not out.collect_failed_note:
        out.collect_failed_note = (
            "동기화 전체 시간 상한(3분)에 도달해, 그때까지 찾은 항목만 반영합니다."
        )

    if not _past(sync_deadline):
        _prog({"phase": "calendar_extra", "course_index": n_courses, "course_total": n_courses, "course_name": ""})
        t_cal = time.perf_counter()
        _step_log("단계: 월간 캘린더에서 추가 수집 시작")
        logger.info("[ETL] 캘린더 화면에서 활동 링크 추가 수집")
        n_cal = _append_assignments_from_calendar_view(driver, out, sync_deadline=sync_deadline)
        _step_log(f"단계: 월간 캘린더 추가 수집 완료 ({time.perf_counter() - t_cal:.2f}s) — 링크 {n_cal}개")
        if out.courses_found == 0 and n_cal > 0:
            out.courses_found = 1
    else:
        logger.info("[ETL] 전체 시간 제한으로 캘린더 추가 수집 생략")

    _prog({"phase": "done", "course_index": n_courses, "course_total": n_courses, "course_name": ""})
    logger.info(
        "[ETL] collect 완료 — 강의 %d, 과제링크 %d, 퀴즈 %d, 공지키워드 %d, 신규항목 %d",
        out.courses_found,
        out.assign_links_found,
        out.quiz_links_found,
        out.announcement_keyword_hits,
        len(out.new_items),
    )


def collect_etl_activities_with_existing_driver(
    driver: WebDriver,
    seen_ids: set[str],
    *,
    sync_deadline: float | None = None,
    progress_cb: Callable[[dict], None] | None = None,
) -> CollectResult:
    """이미 열린 브라우저에서만 수집(세션은 호출 전에 로그인 완료되어 있어야 함)."""
    out = CollectResult(updated_seen=set(seen_ids))
    out.login_ok = True
    dl = sync_deadline if sync_deadline is not None else time.monotonic() + SYNC_MAX_SEC
    _collect_assignments_loop(driver, out, sync_deadline=dl, progress_cb=progress_cb)
    return out


def collect_etl_activities(
    username: str,
    password: str,
    seen_ids: set[str],
    *,
    headless: bool = True,
    pause_before_close_sec: float = 0.0,
    keep_browser_open: bool = False,
    browser: str = "chrome",
) -> CollectResult:
    """
    eTL에서 과제·퀴즈·공지(중간/기말 키워드)를 수집.
    `seen_ids`에 없는 항목만 `new_items`에 넣고, 과제·퀴즈는 마감 페이지를 추가 조회.
    """
    out = CollectResult(updated_seen=set(seen_ids))
    driver = get_driver(headless=headless, browser=browser)
    try:
        ok, note = login(driver, username, password, allow_interactive_mfa=not headless)
        out.login_ok = ok
        out.login_note = (note or None) if not ok else None
        if not ok:
            return out

        _collect_assignments_loop(driver, out)
    finally:
        try:
            if pause_before_close_sec > 0:
                time.sleep(pause_before_close_sec)
        except Exception:
            pass
        # 헤드리스가 아니면 기본적으로 창을 닫지 않음(로그인·MFA 확인용). headless+keep 열면 디버깅용으로 유지.
        leave_open = (not headless) or bool(keep_browser_open)
        if not leave_open:
            driver.quit()
    return out


def collect_assignments(
    username: str,
    password: str,
    seen_ids: set[str],
    *,
    headless: bool = True,
    pause_before_close_sec: float = 0.0,
    browser: str = "chrome",
) -> tuple[list[dict], set[str]]:
    """기존 호환: (new_items, updated_seen)"""
    r = collect_etl_activities(
        username,
        password,
        seen_ids,
        headless=headless,
        pause_before_close_sec=pause_before_close_sec,
        keep_browser_open=False,
        browser=browser,
    )
    return r.new_items, r.updated_seen


def load_seen_from_file() -> set[str]:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_to_file(seen: set[str]) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def get_new_assignments_cli(
    username: str, password: str, headless: bool = True, browser: str = "chrome"
) -> list[dict]:
    seen = load_seen_from_file()
    r = collect_etl_activities(username, password, seen, headless=headless, browser=browser)
    save_seen_to_file(r.updated_seen)
    return r.new_items


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    u = os.getenv("ETL_USERNAME", "")
    p = os.getenv("ETL_PASSWORD", "")
    br = (os.getenv("ETL_BROWSER") or "chrome").strip().lower()
    r = collect_etl_activities(u, p, set(), headless=False, browser=br)
    print(
        "login_ok:",
        r.login_ok,
        "courses:",
        r.courses_found,
        "assign:",
        r.assign_links_found,
        "quiz:",
        r.quiz_links_found,
        "ann_hits:",
        r.announcement_keyword_hits,
    )
    for a in r.new_items:
        t = a.get("activity_type") or "assign"
        tag = {"quiz": "퀴즈", "announcement_midterm": "공지·중간", "announcement_final": "공지·기말"}.get(
            t, "과제"
        )
        print(f"- [{tag}] [{a['subject']}] {a['title']} | 마감: {a['deadline']}")
