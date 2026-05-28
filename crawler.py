"""
crawler.py
광운대학교 학사 일정, LMS, 에브리타임 데이터 수집 모듈

[아키텍처 개요]
─────────────────────────────────────────────────────────
이 파일(crawler.py)은 기존 크롤링 모듈입니다.
새로 추가된 파일과의 관계:

  crawler.py         ← 지금 이 파일 (학사일정 / LMS / 에브리타임 크롤링)
  klas_crawler.py    ← 신규: 실제 KLAS 로그인 + 오늘 할 일 수집
  llm_client.py      ← LLM API 호출 (변경 없음)
  todo_generator.py  ← TODO 생성 파이프라인 (변경 없음)
  app.py             ← 신규: Flask 웹 서버 + 로그인 UI + 대시보드

[데이터 흐름]
  app.py
    └─ KLASClient (klas_crawler.py) ─→ 로그인 인증 + 오늘 할 일 수집
    └─ DataCollector (crawler.py)   ─→ 학사일정 + LMS 과제 + 에브리타임 수집
         └─ TodoGenerator (todo_generator.py) ─→ LLM 기반 TODO 생성
─────────────────────────────────────────────────────────
"""

import re
import time
import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class AcademicEvent:
    """
    학사 일정 이벤트
    [연동] todo_generator.py의 TodoGenerator.generate()에서
           CrawledData.academic_events 필드로 사용됩니다.
    """
    title: str
    start_date: date
    end_date: Optional[date] = None
    category: str = ""          # 수강신청 / 시험 / 등록 / 휴교 등
    source: str = "kwangwoon"

@dataclass
class LMSAssignment:
    """
    LMS 과제/퀴즈
    [연동] todo_generator.py의 TodoGenerator.generate()에서
           CrawledData.lms_assignments 필드로 사용됩니다.
    """
    course_name: str
    title: str
    due_date: Optional[datetime] = None
    assignment_type: str = "과제"   # 과제 / 퀴즈 / 토론
    description: str = ""
    source: str = "lms"

@dataclass
class EverytimePost:
    """
    에브리타임 게시글
    [변경 안내] 직접 크롤링 대신 사용자가 ICS URL을 붙여넣는 방식으로
               EverytimeCalendarParser(별도 구현 가능)로 대체를 권장합니다.
               현재는 기존 크롤러 코드를 유지합니다.
    [연동] todo_generator.py의 TodoGenerator.generate()에서
           CrawledData.everytime_posts 필드로 사용됩니다.
    """
    board: str              # 시험정보 / 강의평가 / 자유 등
    title: str
    body: str
    posted_at: Optional[datetime] = None
    keywords: List[str] = field(default_factory=list)
    source: str = "everytime"


# ──────────────────────────────────────────────
# 광운대 학사 일정 크롤러
# ──────────────────────────────────────────────

class KwangwoonAcademicCalendarCrawler:
    """
    광운대학교 학사 일정 페이지 크롤러
    URL: https://www.kw.ac.kr/ko/life/academic-calendar.jsp

    [연동] klas_crawler.py의 KLASClient._fetch_academic_calendar()에서도
           동일한 URL을 사용해 이번 주 학사일정을 수집합니다.
           - KLASClient: 로그인 세션 기반, 7일 이내 이벤트만 필터링
           - 이 클래스:  로그인 불필요, 학기 전체 이벤트 수집
           용도에 따라 선택해서 사용하세요.
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

    def _parse_date_range(self, raw: str) -> Tuple[Optional[date], Optional[date]]:
        """
        '2025.03.04 ~ 2025.03.07' 또는 '2025.03.04' 형태 파싱
        [참고] klas_crawler.py의 KLASClient._parse_simple_date()와
               유사한 역할을 합니다. 날짜 파싱 로직을 통합하려면
               별도 utils.py로 분리를 고려하세요.
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

    def crawl(self) -> List[AcademicEvent]:
        logger.info(f"[광운대] 학사 일정 크롤링 시작 ({self.year}년 {self.semester}학기)")
        try:
            resp = self.session.get(self.CALENDAR_URL, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"[광운대] 요청 실패: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        events: List[AcademicEvent] = []

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

    def _filter_by_semester(self, events: List[AcademicEvent]) -> List[AcademicEvent]:
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

    [KLAS vs LMS 차이]
    ┌──────────────┬─────────────────────────────┬──────────────────────────────┐
    │              │ klas_crawler.KLASClient      │ crawler.LMSCrawler           │
    ├──────────────┼─────────────────────────────┼──────────────────────────────┤
    │ 로그인 대상  │ klas.kw.ac.kr (KLAS)         │ lms.kw.ac.kr (e-루리/Moodle) │
    │ 수집 내용    │ 오늘 할 일 대시보드 전체     │ 과제/퀴즈 마감 목록          │
    │ 사용 위치    │ app.py 웹 대시보드           │ todo_generator.py 파이프라인  │
    └──────────────┴─────────────────────────────┴──────────────────────────────┘
    두 시스템의 계정(학번/비밀번호)은 동일하나 URL이 다릅니다.

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
        """
        마감일 문자열 파싱
        [참고] klas_crawler.py의 KLASClient._parse_due()와 동일한 역할입니다.
               두 파서를 utils.py의 공통 함수로 통합하면 중복을 줄일 수 있습니다.
        """
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

    def crawl(self) -> List[LMSAssignment]:
        if not self._logged_in:
            success = self.login()
            if not success:
                return []

        logger.info("[LMS] 마감 임박 과제 수집 중...")
        assignments: List[LMSAssignment] = []

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

    ⚠️  [크롤링 한계 안내]
    에브리타임은 React SPA 기반 동적 렌더링을 사용하므로
    BeautifulSoup 단독으로는 파싱이 되지 않을 수 있습니다.
    아래 두 가지 대안을 권장합니다:

    [대안 A] ICS URL 방식 (권장)
        에브리타임 앱 → 시간표 → 외부 공유 URL 복사
        → 프로그램에 붙여넣기 → ics 파일 파싱
        구현 예시:
            import requests
            from icalendar import Calendar
            ics_url = "https://everytime.kr/@사용자토큰/ical"
            cal = Calendar.from_ical(requests.get(ics_url).content)

    [대안 B] 사용자 직접 텍스트 입력
        에브리타임에서 강의평/게시글 복사 → 붙여넣기
        → llm_client.py의 ClaudeClient로 분석 요약
        구현 예시 (todo_generator.py에서 활용):
            client = create_llm_client("claude")
            summary = client.chat(
                user_message=f"다음 강의평을 분석해서 핵심만 요약해줘:\\n{user_pasted_text}",
                system_prompt="대학생 학습 도우미"
            )

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

    def __init__(self, username: str, password: str, keywords: List[str] = None):
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

    def _extract_keywords(self, text: str) -> List[str]:
        return [kw for kw in self.filter_keywords if kw in text]

    def _parse_posts(self, html: str, board_name: str) -> List[EverytimePost]:
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

    def crawl(self, boards: List[str] = None) -> List[EverytimePost]:
        if not self._logged_in:
            success = self.login()
            if not success:
                return []

        target_boards = boards or list(self.BOARD_URLS.keys())
        all_posts: List[EverytimePost] = []

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
    """
    모든 크롤러의 수집 결과를 담는 컨테이너

    [연동] todo_generator.py의 TodoGenerator.generate(data: CrawledData)에
           그대로 전달됩니다.

    [app.py와의 관계]
    app.py의 웹 대시보드는 klas_crawler.KLASClient를 직접 사용하므로
    CrawledData를 거치지 않습니다. CrawledData는 CLI 파이프라인
    (todo_generator.run_pipeline)에서 사용됩니다.

    두 파이프라인 비교:
      [웹 대시보드]  app.py → KLASClient → TodayTask 목록 → 브라우저 렌더링
      [CLI 파이프라인] DataCollector → CrawledData → TodoGenerator → TodoList 출력
    """
    academic_events: List[AcademicEvent] = field(default_factory=list)
    lms_assignments: List[LMSAssignment] = field(default_factory=list)
    everytime_posts: List[EverytimePost] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"학사일정 {len(self.academic_events)}건 | "
            f"LMS 과제 {len(self.lms_assignments)}건 | "
            f"에브리타임 {len(self.everytime_posts)}건"
        )


class DataCollector:
    """
    모든 크롤러를 통합 실행하는 퍼사드 클래스

    [사용 위치]
    - todo_generator.py의 run_pipeline() 함수에서 호출됩니다.
    - app.py 웹 서버는 이 클래스 대신 klas_crawler.KLASClient를
      직접 사용합니다. (KLAS 세션 재사용을 위해)

    [확장 방법]
    웹 대시보드(app.py)에서 LLM 기반 TODO 생성까지 원할 때:
        # app.py의 api_tasks() 라우트에서 추가 가능
        from crawler import DataCollector
        from todo_generator import run_pipeline

        collector = DataCollector(lms_username=..., lms_password=...)
        data = collector.collect_all()
        todo_list = run_pipeline(data, llm_provider="claude")
    """

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
        # [연동] 수집된 결과는 CrawledData.academic_events에 저장되고
        #        todo_generator.py의 PromptTemplates.build_todo_prompt()에서
        #        LLM 프롬프트로 변환됩니다.
        data.academic_events = self.academic_crawler.crawl()

        # 2. LMS 과제
        # [연동] lms_username이 없으면 건너뜁니다.
        #        app.py에서 받은 학번/비밀번호를 여기에도 전달하면
        #        LMS 과제까지 함께 수집할 수 있습니다.
        if self.lms_crawler:
            data.lms_assignments = self.lms_crawler.crawl()
        else:
            logger.info("[LMS] 계정 미설정 - 건너뜁니다")

        # 3. 에브리타임
        # [주의] 동적 렌더링 이슈로 수집이 안 될 수 있습니다.
        #        EverytimeCrawler 클래스의 docstring에서 대안을 확인하세요.
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
    # ── CLI 파이프라인 단독 실행 예시 ──
    # 웹 대시보드(app.py)를 쓰지 않고 터미널에서 직접 실행할 때 사용합니다.
    #
    # 웹 대시보드와 함께 쓰는 경우:
    #   python app.py 실행 후 http://localhost:5000 접속
    #   → 로그인하면 klas_crawler.KLASClient가 자동으로 데이터를 수집합니다.

    collector = DataCollector(
        lms_username="학번",
        lms_password="비밀번호",
        everytime_username="에브리타임_아이디",   # 수집 안 될 수 있음 (위 주의사항 참고)
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
