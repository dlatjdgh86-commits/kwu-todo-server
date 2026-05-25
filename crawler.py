"""
crawler.py
광운대학교 학사 일정, LMS, 에브리타임 데이터 수집 모듈
"""

import re
import time
import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class AcademicEvent:
    """학사 일정 이벤트"""
    title: str
    start_date: date
    end_date: Optional[date] = None
    category: str = ""          # 수강신청 / 시험 / 등록 / 휴교 등
    source: str = "kwangwoon"

@dataclass
class LMSAssignment:
    """LMS 과제/퀴즈"""
    course_name: str
    title: str
    due_date: Optional[datetime] = None
    assignment_type: str = "과제"   # 과제 / 퀴즈 / 토론
    description: str = ""
    source: str = "lms"

@dataclass
class EverytimePost:
    """에브리타임 게시글"""
    board: str              # 시험정보 / 강의평가 / 자유 등
    title: str
    body: str
    posted_at: Optional[datetime] = None
    keywords: list[str] = field(default_factory=list)
    source: str = "everytime"


# ──────────────────────────────────────────────
# 광운대 학사 일정 크롤러
# ──────────────────────────────────────────────

class KwangwoonAcademicCalendarCrawler:
    """
    광운대학교 학사 일정 페이지 크롤러
    URL: https://www.kw.ac.kr/ko/life/academic-calendar.jsp
    """

    BASE_URL = "https://www.kw.ac.kr"
    CALENDAR_URL = "https://www.kw.ac.kr/ko/life/academic-calendar.jsp"

    CATEGORY_KEYWORDS = {
        "수강신청": ["수강신청", "수강변경", "수강취소"],
        "시험": ["중간고사", "기말고사", "시험"],
        "등록": ["등록금", "등록 기간", "분할납부"],
        "휴교": ["휴교", "공휴일", "개교기념"],
        "졸업": ["졸업", "학위"],
        "성적": ["성적", "이의신청"],
    }

    def __init__(self, year: int = None, semester: int = None):
        self.year = year or datetime.today().year
        self.semester = semester or (1 if datetime.today().month <= 6 else 2)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        })

    def _detect_category(self, title: str) -> str:
        for cat, keywords in self.CATEGORY_KEYWORDS.items():
            if any(kw in title for kw in keywords):
                return cat
        return "기타"

    def _parse_date_range(self, raw: str) -> tuple[Optional[date], Optional[date]]:
        """
        '2025.03.04 ~ 2025.03.07' 또는 '2025.03.04' 형태 파싱
        """
        raw = raw.strip()
        parts = re.split(r"[~\-–]", raw)
        def to_date(s: str) -> Optional[date]:
            s = s.strip().replace(" ", "")
            for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"):
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None

        start = to_date(parts[0]) if parts else None
        end = to_date(parts[1]) if len(parts) > 1 else None
        return start, end

    def crawl(self) -> list[AcademicEvent]:
        logger.info(f"[광운대] 학사 일정 크롤링 시작 ({self.year}년 {self.semester}학기)")
        try:
            resp = self.session.get(self.CALENDAR_URL, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"[광운대] 요청 실패: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        events: list[AcademicEvent] = []

        # ── 전략 1: <table> 기반 파싱 (광운대 학사일정 테이블 구조)
        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:  # 헤더 제외
                cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cols) < 2:
                    continue
                # 보통 [날짜, 내용] 또는 [월, 날짜, 내용] 구조
                date_str, title = cols[0], cols[-1]
                start, end = self._parse_date_range(date_str)
                if start and title:
                    events.append(AcademicEvent(
                        title=title,
                        start_date=start,
                        end_date=end,
                        category=self._detect_category(title),
                    ))

        # ── 전략 2: dl/dt/dd 리스트 구조 대응
        for dl in soup.find_all("dl"):
            dt_tags = dl.find_all("dt")
            dd_tags = dl.find_all("dd")
            for dt, dd in zip(dt_tags, dd_tags):
                date_str = dt.get_text(strip=True)
                title = dd.get_text(strip=True)
                start, end = self._parse_date_range(date_str)
                if start and title:
                    events.append(AcademicEvent(
                        title=title,
                        start_date=start,
                        end_date=end,
                        category=self._detect_category(title),
                    ))

        # ── 학기 필터링
        events = self._filter_by_semester(events)
        logger.info(f"[광운대] 수집된 이벤트: {len(events)}건")
        return events

    def _filter_by_semester(self, events: list[AcademicEvent]) -> list[AcademicEvent]:
        if self.semester == 1:
            start_month, end_month = 2, 8
        else:
            start_month, end_month = 8, 2

        filtered = []
        for e in events:
            m = e.start_date.month
            if self.semester == 1 and start_month <= m <= end_month:
                filtered.append(e)
            elif self.semester == 2 and (m >= start_month or m <= end_month):
                filtered.append(e)
        return filtered


# ──────────────────────────────────────────────
# LMS 크롤러 (광운대 e-루리 기반)
# ──────────────────────────────────────────────

class LMSCrawler:
    """
    광운대 LMS (e-루리, Moodle 기반) 과제/퀴즈 수집
    로그인 세션을 유지하며 대시보드에서 마감 임박 항목을 파싱합니다.

    사용법:
        crawler = LMSCrawler(username="학번", password="비밀번호")
        assignments = crawler.crawl()
    """

    LOGIN_URL = "https://lms.kw.ac.kr/login/index.php"
    DASHBOARD_URL = "https://lms.kw.ac.kr/my/"
    UPCOMING_URL = "https://lms.kw.ac.kr/calendar/view.php?view=upcoming"

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            )
        })
        self._logged_in = False

    def login(self) -> bool:
        try:
            resp = self.session.get(self.LOGIN_URL, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # logintoken 추출 (Moodle CSRF)
            token_input = soup.find("input", {"name": "logintoken"})
            token = token_input["value"] if token_input else ""

            payload = {
                "username": self.username,
                "password": self.password,
                "logintoken": token,
                "anchor": "",
            }
            resp = self.session.post(self.LOGIN_URL, data=payload, timeout=15)
            self._logged_in = "로그아웃" in resp.text or "Log out" in resp.text
            if self._logged_in:
                logger.info("[LMS] 로그인 성공")
            else:
                logger.warning("[LMS] 로그인 실패 - 계정 정보를 확인하세요")
            return self._logged_in
        except requests.RequestException as e:
            logger.error(f"[LMS] 로그인 오류: {e}")
            return False

    def _parse_due_date(self, raw: str) -> Optional[datetime]:
        patterns = [
            "%Y년 %m월 %d일 %H시 %M분",
            "%Y-%m-%d %H:%M",
            "%d %B %Y, %I:%M %p",
            "%A, %d %B %Y, %I:%M %p",
        ]
        raw = raw.strip()
        for fmt in patterns:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        # 숫자만 추출해서 시도
        nums = re.findall(r"\d+", raw)
        if len(nums) >= 3:
            try:
                return datetime(int(nums[0]), int(nums[1]), int(nums[2]))
            except Exception:
                pass
        return None

    def _detect_type(self, title: str) -> str:
        if any(k in title for k in ["퀴즈", "quiz", "Quiz"]):
            return "퀴즈"
        if any(k in title for k in ["토론", "discussion"]):
            return "토론"
        if any(k in title for k in ["출석", "attendance"]):
            return "출석"
        return "과제"

    def crawl(self) -> list[LMSAssignment]:
        if not self._logged_in:
            success = self.login()
            if not success:
                return []

        logger.info("[LMS] 마감 임박 과제 수집 중...")
        assignments: list[LMSAssignment] = []

        try:
            resp = self.session.get(self.UPCOMING_URL, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Moodle upcoming events 파싱
            for event_div in soup.find_all("div", class_=re.compile(r"event")):
                title_tag = event_div.find(["h3", "h4", "a"], class_=re.compile(r"name|title"))
                course_tag = event_div.find(class_=re.compile(r"course|subject"))
                date_tag = event_div.find(class_=re.compile(r"date|time|due"))

                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                course = course_tag.get_text(strip=True) if course_tag else "알 수 없음"
                due = self._parse_due_date(date_tag.get_text(strip=True)) if date_tag else None

                assignments.append(LMSAssignment(
                    course_name=course,
                    title=title,
                    due_date=due,
                    assignment_type=self._detect_type(title),
                ))

        except requests.RequestException as e:
            logger.error(f"[LMS] 수집 오류: {e}")

        logger.info(f"[LMS] 수집된 과제: {len(assignments)}건")
        return assignments


# ──────────────────────────────────────────────
# 에브리타임 크롤러
# ──────────────────────────────────────────────

class EverytimeCrawler:
    """
    에브리타임 게시판 크롤러
    - 로그인 후 쿠키 기반 세션 유지
    - 시험정보 / 강의평가 / 공지 게시판 수집
    - 키워드 기반 필터링 지원

    주의: 에브리타임 이용약관을 준수하여 과도한 요청을 지양하세요.
    """

    BASE_URL = "https://everytime.kr"
    LOGIN_URL = "https://everytime.kr/api/v1/auth/login"
    BOARD_URLS = {
        "시험정보": "https://everytime.kr/exam",
        "강의평가": "https://everytime.kr/lecture",
        "학교생활": "https://everytime.kr/community",
    }
    # 학사 관련 필터 키워드
    ACADEMIC_KEYWORDS = [
        "시험", "과제", "마감", "제출", "중간", "기말", "퀴즈",
        "레포트", "발표", "팀플", "프로젝트", "휴강", "보강",
    ]

    def __init__(self, username: str, password: str, keywords: list[str] = None):
        self.username = username
        self.password = password
        self.filter_keywords = keywords or self.ACADEMIC_KEYWORDS
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Referer": self.BASE_URL,
        })
        self._logged_in = False

    def login(self) -> bool:
        try:
            resp = self.session.post(
                self.LOGIN_URL,
                data={"userid": self.username, "password": self.password},
                timeout=15,
            )
            # 에브리타임 API는 JSON 응답
            data = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {}
            self._logged_in = resp.status_code == 200 and data.get("status") != "fail"
            if self._logged_in:
                logger.info("[에브리타임] 로그인 성공")
            else:
                logger.warning("[에브리타임] 로그인 실패")
            return self._logged_in
        except Exception as e:
            logger.error(f"[에브리타임] 로그인 오류: {e}")
            return False

    def _extract_keywords(self, text: str) -> list[str]:
        return [kw for kw in self.filter_keywords if kw in text]

    def _parse_posts(self, html: str, board_name: str) -> list[EverytimePost]:
        soup = BeautifulSoup(html, "html.parser")
        posts = []

        for article in soup.find_all("article"):
            title_tag = article.find(class_=re.compile(r"title|subject"))
            body_tag = article.find(class_=re.compile(r"content|text|body"))
            time_tag = article.find("time") or article.find(class_=re.compile(r"date|time"))

            title = title_tag.get_text(strip=True) if title_tag else ""
            body = body_tag.get_text(strip=True) if body_tag else ""
            full_text = title + " " + body

            # 학사 관련 키워드가 있는 게시글만 수집
            found_keywords = self._extract_keywords(full_text)
            if not found_keywords:
                continue

            posted_at = None
            if time_tag:
                raw_time = time_tag.get("datetime") or time_tag.get_text(strip=True)
                try:
                    posted_at = datetime.fromisoformat(raw_time)
                except Exception:
                    pass

            posts.append(EverytimePost(
                board=board_name,
                title=title,
                body=body[:500],   # 500자 제한
                posted_at=posted_at,
                keywords=found_keywords,
            ))

        return posts

    def crawl(self, boards: list[str] = None) -> list[EverytimePost]:
        if not self._logged_in:
            success = self.login()
            if not success:
                return []

        target_boards = boards or list(self.BOARD_URLS.keys())
        all_posts: list[EverytimePost] = []

        for board_name in target_boards:
            url = self.BOARD_URLS.get(board_name)
            if not url:
                continue
            try:
                logger.info(f"[에브리타임] '{board_name}' 게시판 수집 중...")
                resp = self.session.get(url, timeout=15)
                posts = self._parse_posts(resp.text, board_name)
                all_posts.extend(posts)
                time.sleep(1.5)  # 서버 부하 방지
            except requests.RequestException as e:
                logger.error(f"[에브리타임] '{board_name}' 수집 오류: {e}")

        logger.info(f"[에브리타임] 수집된 게시글: {len(all_posts)}건")
        return all_posts


# ──────────────────────────────────────────────
# 통합 수집기
# ──────────────────────────────────────────────

@dataclass
class CrawledData:
    academic_events: list[AcademicEvent] = field(default_factory=list)
    lms_assignments: list[LMSAssignment] = field(default_factory=list)
    everytime_posts: list[EverytimePost] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"학사일정 {len(self.academic_events)}건 | "
            f"LMS 과제 {len(self.lms_assignments)}건 | "
            f"에브리타임 {len(self.everytime_posts)}건"
        )


class DataCollector:
    """모든 크롤러를 통합 실행하는 퍼사드 클래스"""

    def __init__(
        self,
        lms_username: str = "",
        lms_password: str = "",
        everytime_username: str = "",
        everytime_password: str = "",
        year: int = None,
        semester: int = None,
    ):
        self.academic_crawler = KwangwoonAcademicCalendarCrawler(year=year, semester=semester)
        self.lms_crawler = LMSCrawler(lms_username, lms_password) if lms_username else None
        self.everytime_crawler = (
            EverytimeCrawler(everytime_username, everytime_password)
            if everytime_username else None
        )

    def collect_all(self) -> CrawledData:
        data = CrawledData()

        # 1. 학사 일정
        data.academic_events = self.academic_crawler.crawl()

        # 2. LMS 과제
        if self.lms_crawler:
            data.lms_assignments = self.lms_crawler.crawl()
        else:
            logger.info("[LMS] 계정 미설정 - 건너뜁니다")

        # 3. 에브리타임
        if self.everytime_crawler:
            data.everytime_posts = self.everytime_crawler.crawl()
        else:
            logger.info("[에브리타임] 계정 미설정 - 건너뜁니다")

        logger.info(f"[수집 완료] {data.summary()}")
        return data


# ──────────────────────────────────────────────
# 실행 예시
# ──────────────────────────────────────────────

if __name__ == "__main__":
    collector = DataCollector(
        lms_username="학번",
        lms_password="비밀번호",
        everytime_username="에브리타임_아이디",
        everytime_password="에브리타임_비밀번호",
    )
    result = collector.collect_all()
    print(result.summary())

    print("\n=== 학사 일정 (상위 5건) ===")
    for e in result.academic_events[:5]:
        print(f"  [{e.category}] {e.title} ({e.start_date})")

    print("\n=== LMS 과제 (상위 5건) ===")
    for a in result.lms_assignments[:5]:
        print(f"  [{a.course_name}] {a.title} - 마감: {a.due_date}")
