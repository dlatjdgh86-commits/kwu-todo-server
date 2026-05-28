"""
crawler.py
광운대학교 데이터 수집 모듈

수집 대상:
  1. 광운대 학사일정 (로그인 불필요)
  2. KLAS 과제/퀴즈  (klas_crawler.KLASClient 세션 재사용)
  3. KLAS 온라인강의 미수강 목록 (klas_crawler.KLASClient 세션 재사용)
"""

import re
import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class AcademicEvent:
    """광운대 학사일정 이벤트"""
    title: str
    start_date: date
    end_date: Optional[date] = None
    category: str = "기타"   # 수강신청 / 시험 / 등록 / 휴교 / 성적 / 졸업 / 기타
    source: str = "kwangwoon"


@dataclass
class KlasAssignment:
    """KLAS 과제 / 퀴즈"""
    course_name: str
    title: str
    task_type: str = "과제"          # 과제 / 퀴즈 / 토론 / 프로젝트
    due_date: Optional[datetime] = None
    due_str: str = ""
    priority: int = 3                # 1=긴급 2=이번주 3=보통
    url: str = ""
    is_submitted: bool = False       # 제출 완료 여부
    source: str = "klas"

    # [추가] 화면 출력용 형식 메서드
    # 예: [과제] 컴퓨터구조: Project (마감: 04/22)
    def display(self) -> str:
        status = "✅" if self.is_submitted else "❌"
        return f"{status} [{self.task_type}] {self.course_name}: {self.title} (마감: {self.due_str})"


@dataclass
class KlasLecture:
    """KLAS 온라인강의 미수강 항목"""
    course_name: str
    title: str
    week: str = ""
    deadline: Optional[datetime] = None
    deadline_str: str = ""
    priority: int = 3
    url: str = ""
    source: str = "klas"

    # [추가] 화면 출력용 형식 메서드
    # 예: [온라인강의] 디지털논리: 13주차 미수강 (D-3)
    def display(self) -> str:
        title = f"{self.week}주차 {self.title}" if self.week else self.title
        return f"❌ [온라인강의] {self.course_name}: {title} ({self.deadline_str})"


@dataclass
class CrawledData:
    """모든 크롤러 수집 결과 컨테이너 — todo_generator.py에 그대로 전달"""
    academic_events: List[AcademicEvent]  = field(default_factory=list)
    assignments:     List[KlasAssignment] = field(default_factory=list)
    lectures:        List[KlasLecture]    = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"학사일정 {len(self.academic_events)}건 | "
            f"과제/퀴즈 {len(self.assignments)}건 | "
            f"미수강강의 {len(self.lectures)}건"
        )


# ──────────────────────────────────────────────
# 광운대 학사일정 크롤러 (로그인 불필요)
# ──────────────────────────────────────────────

class AcademicCalendarCrawler:
    """
    광운대 학사일정 페이지 크롤러
    URL: https://www.kw.ac.kr/ko/life/academic-calendar.jsp
    """

    URL = "https://www.kw.ac.kr/ko/life/academic-calendar.jsp"

    CATEGORY_MAP = {
        "수강신청": ["수강신청", "수강변경", "수강취소"],
        "시험":     ["중간고사", "기말고사", "시험"],
        "등록":     ["등록금", "등록 기간", "분할납부"],
        "휴교":     ["휴교", "공휴일", "개교기념"],
        "졸업":     ["졸업", "학위"],
        "성적":     ["성적", "이의신청"],
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        })

    def _category(self, title: str) -> str:
        for cat, kws in self.CATEGORY_MAP.items():
            if any(kw in title for kw in kws):
                return cat
        return "기타"

    def _parse_date(self, raw: str) -> Optional[date]:
        raw = raw.strip().split("~")[0].strip().replace(" ", "")
        for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    def _parse_date_range(self, raw: str) -> Tuple[Optional[date], Optional[date]]:
        parts = re.split(r"[~\-–]", raw)
        start = self._parse_date(parts[0]) if parts else None
        end   = self._parse_date(parts[1]) if len(parts) > 1 else None
        return start, end

    def crawl(self, days_ahead: int = 60) -> List[AcademicEvent]:
        """
        향후 days_ahead일 이내 학사일정만 반환
        """
        logger.info("[학사일정] 크롤링 시작")
        try:
            resp = self.session.get(self.URL, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"[학사일정] 요청 실패: {e}")
            return []

        soup   = BeautifulSoup(resp.text, "html.parser")
        events: List[AcademicEvent] = []
        today  = date.today()

        # 전략 1: <table> 구조
        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cols) < 2:
                    continue
                start, end = self._parse_date_range(cols[0])
                title = cols[-1]
                if start and title and 0 <= (start - today).days <= days_ahead:
                    events.append(AcademicEvent(
                        title=title, start_date=start, end_date=end,
                        category=self._category(title),
                    ))

        # 전략 2: dl/dt/dd 구조
        for dl in soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                start, end = self._parse_date_range(dt.get_text(strip=True))
                title = dd.get_text(strip=True)
                if start and title and 0 <= (start - today).days <= days_ahead:
                    events.append(AcademicEvent(
                        title=title, start_date=start, end_date=end,
                        category=self._category(title),
                    ))

        # 중복 제거
        seen = set()
        unique = []
        for e in events:
            key = f"{e.title}_{e.start_date}"
            if key not in seen:
                seen.add(key)
                unique.append(e)

        logger.info(f"[학사일정] {len(unique)}건 수집")
        return unique


# ──────────────────────────────────────────────
# KLAS 과제/퀴즈 크롤러 (KLASClient 세션 재사용)
# ──────────────────────────────────────────────

class KlasAssignmentCrawler:
    """
    KLAS 과제 제출 페이지 크롤러
    URL: https://klas.kw.ac.kr/std/lis/evltn/TaskStdPage.do

    klas_crawler.KLASClient로 로그인한 Selenium driver를 받아서
    과목별 과제 목록을 수집합니다.
    """

    TASK_URL = "https://klas.kw.ac.kr/std/lis/evltn/TaskStdPage.do"
    QUIZ_URL = "https://klas.kw.ac.kr/std/lis/evltn/AnytmQuizStdPage.do"
    BASE_URL = "https://klas.kw.ac.kr"

    def __init__(self, driver):
        """
        Args:
            driver: klas_crawler.KLASClient.driver (Selenium WebDriver)
        """
        self.driver = driver

    def _parse_due(self, raw: str) -> Tuple[Optional[datetime], str, int]:
        """
        날짜 문자열 파싱 → (datetime, 표시문자열, 우선순위)
        날짜 범위(2026.04.09 ~ 2026.04.22 23:59:59)에서 종료일 추출
        """
        if not raw:
            return None, "마감일 미정", 3

        raw = raw.strip()
        dt  = None

        patterns = [
            ("%Y.%m.%d %H:%M:%S", r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}"),
            ("%Y.%m.%d %H:%M",    r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}"),
            ("%Y-%m-%d %H:%M:%S", r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"),
            ("%Y-%m-%d %H:%M",    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"),
            ("%Y.%m.%d",          r"\d{4}\.\d{2}\.\d{2}"),
            ("%Y-%m-%d",          r"\d{4}-\d{2}-\d{2}"),
        ]

        for fmt, pattern in patterns:
            # 날짜 범위면 ~ 뒤 종료일 우선 추출
            range_match = re.search(r"~\s*(" + pattern + r")", raw)
            if range_match:
                try:
                    dt = datetime.strptime(range_match.group(1).strip(), fmt)
                    break
                except ValueError:
                    pass
            match = re.search(pattern, raw)
            if match:
                try:
                    dt = datetime.strptime(match.group(), fmt)
                    break
                except ValueError:
                    continue

        if not dt:
            return None, raw[:30], 3

        today = date.today()
        days  = (dt.date() - today).days

        if days < 0:
            return dt, f"⚠️ 마감됨 ({dt.strftime('%m/%d')})", 1
        elif days == 0:
            return dt, f"오늘 {dt.strftime('%H:%M')} 마감", 1
        elif days == 1:
            return dt, f"내일 {dt.strftime('%H:%M')} 마감", 1
        elif days <= 3:
            return dt, f"{days}일 후 ({dt.strftime('%m/%d')})", 2
        elif days <= 7:
            return dt, f"{dt.strftime('%m/%d')} 마감 ({days}일)", 2
        else:
            return dt, f"{dt.strftime('%m/%d')} 마감 ({days}일)", 3

    def _parse_table(self, soup: BeautifulSoup, course_name: str, task_type: str) -> List[KlasAssignment]:
        """과제/퀴즈 테이블 파싱"""
        assignments = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            # 과제/퀴즈 테이블 판별
            if not any(h in headers for h in ["과제 제목", "제목", "퀴즈명", "시험명"]):
                continue

            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue

                title = cols[1].get_text(strip=True) if len(cols) > 1 else cols[0].get_text(strip=True)
                if not title or len(title) < 2:
                    continue

                # 날짜 컬럼 찾기
                date_text = ""
                for col in cols:
                    text = col.get_text(strip=True)
                    if re.search(r"\d{4}[-./]\d{2}[-./]\d{2}", text):
                        date_text = text
                        break

                due_date, due_str, priority = self._parse_due(date_text)

                # 이미 7일 이상 지난 과제는 제외
                if due_date and (due_date.date() - date.today()).days < -7:
                    continue

                # 제출 상태 확인
                status_text = " ".join(col.get_text(strip=True) for col in cols)
                is_submitted = "제출" in status_text and "미제출" not in status_text

                # 링크 추출
                link_tag = cols[1].find("a") if len(cols) > 1 else None
                url = ""
                if link_tag and link_tag.get("href"):
                    href = link_tag.get("href")
                    url = href if href.startswith("http") else self.BASE_URL + href

                assignments.append(KlasAssignment(
                    course_name=course_name,
                    title=title,
                    task_type=task_type,
                    due_date=due_date,
                    due_str=due_str,
                    priority=priority,
                    url=url,
                    is_submitted=is_submitted,
                ))

        return assignments

    def crawl_assignments(self, course_name: str = "전체") -> List[KlasAssignment]:
        """과제 수집"""
        import time as _time
        assignments = []
        try:
            self.driver.get(self.TASK_URL)
            _time.sleep(2)
            soup = BeautifulSoup(self.driver.page_source, "html.parser")

            # 과목 드롭다운이 있으면 과목별로 순회
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import Select
            selects = self.driver.find_elements(By.CSS_SELECTOR, "select")
            course_select = None
            for sel in selects:
                opts = sel.find_elements(By.TAG_NAME, "option")
                if len(opts) > 1:
                    course_select = sel
                    break

            if course_select:
                options = [(opt.get_attribute("value"), opt.text.strip())
                           for opt in course_select.find_elements(By.TAG_NAME, "option")
                           if opt.get_attribute("value")]
                for val, name in options:
                    try:
                        Select(course_select).select_by_value(val)
                        _time.sleep(1.5)
                        soup = BeautifulSoup(self.driver.page_source, "html.parser")
                        assignments.extend(self._parse_table(soup, name, "과제"))
                        # 드롭다운 다시 찾기 (DOM 재렌더링 대비)
                        course_select = self.driver.find_element(By.CSS_SELECTOR, "select")
                    except Exception as e:
                        logger.warning(f"[과제] {name} 파싱 실패: {e}")
            else:
                assignments.extend(self._parse_table(soup, course_name, "과제"))

        except Exception as e:
            logger.error(f"[과제] 수집 오류: {e}")

        logger.info(f"[과제] {len(assignments)}건 수집")
        return assignments

    def crawl_quizzes(self, course_name: str = "전체") -> List[KlasAssignment]:
        """퀴즈 수집"""
        import time as _time
        quizzes = []
        try:
            self.driver.get(self.QUIZ_URL)
            _time.sleep(2)
            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            quizzes = self._parse_table(soup, course_name, "퀴즈")
        except Exception as e:
            logger.error(f"[퀴즈] 수집 오류: {e}")

        logger.info(f"[퀴즈] {len(quizzes)}건 수집")
        return quizzes


# ──────────────────────────────────────────────
# KLAS 온라인강의 미수강 크롤러
# ──────────────────────────────────────────────

class KlasLectureCrawler:
    """
    KLAS 온라인강의 출석 현황 크롤러
    URL: https://klas.kw.ac.kr/std/lis/evltn/LctrumStdPage.do
    미수강(출석 X) 강의만 수집합니다.
    """

    LECTURE_URL = "https://klas.kw.ac.kr/std/lis/evltn/LctrumStdPage.do"
    BASE_URL    = "https://klas.kw.ac.kr"

    def __init__(self, driver):
        self.driver = driver

    def crawl(self) -> List[KlasLecture]:
        """미수강 온라인강의 수집"""
        import time as _time
        lectures = []
        try:
            self.driver.get(self.LECTURE_URL)
            _time.sleep(2)
            soup = BeautifulSoup(self.driver.page_source, "html.parser")

            for table in soup.find_all("table"):
                for row in table.find_all("tr")[1:]:
                    cols = row.find_all("td")
                    if len(cols) < 3:
                        continue

                    row_text = " ".join(c.get_text(strip=True) for c in cols)

                    # 미수강(X 또는 미시청) 항목만 추출
                    if not any(kw in row_text for kw in ["미시청", "X", "미완료"]):
                        continue

                    # 강의명, 주차, 마감일 추출
                    title   = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                    week    = cols[0].get_text(strip=True)
                    course  = ""
                    deadline_str = ""

                    for col in cols:
                        text = col.get_text(strip=True)
                        if re.search(r"\d{4}[-./]\d{2}[-./]\d{2}", text):
                            deadline_str = text
                            break

                    if not title or len(title) < 2:
                        continue

                    # 마감일 파싱
                    dl_dt = None
                    dl_str = "마감일 미정"
                    priority = 3
                    if deadline_str:
                        match = re.search(r"\d{4}[-./]\d{2}[-./]\d{2}", deadline_str)
                        if match:
                            for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
                                try:
                                    dl_dt = datetime.strptime(match.group(), fmt)
                                    days = (dl_dt.date() - date.today()).days
                                    if days < 0:
                                        dl_str = f"⚠️ 기간 초과 ({dl_dt.strftime('%m/%d')})"
                                        priority = 1
                                    elif days <= 2:
                                        dl_str = f"D-{days} ({dl_dt.strftime('%m/%d')})"
                                        priority = 1
                                    elif days <= 7:
                                        dl_str = f"D-{days} ({dl_dt.strftime('%m/%d')})"
                                        priority = 2
                                    else:
                                        dl_str = f"D-{days} ({dl_dt.strftime('%m/%d')})"
                                        priority = 3
                                    break
                                except ValueError:
                                    continue

                    link_tag = cols[1].find("a") if len(cols) > 1 else None
                    url = ""
                    if link_tag and link_tag.get("href"):
                        href = link_tag.get("href")
                        url = href if href.startswith("http") else self.BASE_URL + href

                    lectures.append(KlasLecture(
                        course_name=course,
                        title=title,
                        week=week,
                        deadline=dl_dt,
                        deadline_str=dl_str,
                        priority=priority,
                        url=url,
                    ))

        except Exception as e:
            logger.error(f"[온라인강의] 수집 오류: {e}")

        logger.info(f"[온라인강의] 미수강 {len(lectures)}건 수집")
        return lectures


# ──────────────────────────────────────────────
# 통합 수집기
# ──────────────────────────────────────────────

class DataCollector:
    """
    모든 크롤러를 통합 실행하는 퍼사드 클래스
    klas_crawler.KLASClient로 로그인 후 driver를 전달받아 사용합니다.

    사용 예시:
        from klas_crawler import KLASClient
        client = KLASClient()
        client.login(student_id, password)

        collector = DataCollector(klas_driver=client.driver)
        data = collector.collect_all()
    """

    def __init__(self, klas_driver=None):
        """
        Args:
            klas_driver: KLASClient.driver (Selenium WebDriver, 로그인 완료 상태)
                         None이면 학사일정만 수집
        """
        self.klas_driver = klas_driver
        self.calendar_crawler = AcademicCalendarCrawler()

    def collect_all(self) -> CrawledData:
        data = CrawledData()

        # 1. 학사일정 (로그인 불필요)
        logger.info("[DataCollector] 학사일정 수집 시작")
        data.academic_events = self.calendar_crawler.crawl()

        # 2. KLAS 과제/퀴즈 (드라이버 필요)
        if self.klas_driver:
            logger.info("[DataCollector] KLAS 과제/퀴즈 수집 시작")
            assign_crawler = KlasAssignmentCrawler(self.klas_driver)
            data.assignments.extend(assign_crawler.crawl_assignments())
            data.assignments.extend(assign_crawler.crawl_quizzes())

            # 3. 온라인강의 미수강 수집
            logger.info("[DataCollector] 온라인강의 미수강 수집 시작")
            lec_crawler = KlasLectureCrawler(self.klas_driver)
            data.lectures = lec_crawler.crawl()
        else:
            logger.info("[DataCollector] KLAS 드라이버 없음 — 학사일정만 수집")

        logger.info(f"[DataCollector] 수집 완료: {data.summary()}")
        return data
