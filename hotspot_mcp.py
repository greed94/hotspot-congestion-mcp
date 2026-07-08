"""
핫플 혼잡도 비서 - MVP MCP 서버 (AGENTIC PLAYER 10 예선)
데이터: 서울 실시간 도시데이터 citydata (data.seoul.go.kr)
- 실시간 인구 혼잡도(4단계) + 향후 12시간 예측 + 실시간 상권
전송: streamable-http (PlayMCP/카카오클라우드 원격 MCP)

필드명·혼잡도 4단계·POI 목록은 실제 API 응답과 대조 확인됨(T2, 121곳).
"""
import os
import re
import json
import time
import asyncio
import difflib
import threading
import urllib.parse
from typing import Any

import logging

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.getLogger("httpx").setLevel(logging.WARNING)  # 요청 로그에 API 키(URL 내)가 남지 않게


def _load_dotenv(path: str = ".env") -> None:
    """로컬 실행 편의: .env 값을 환경변수로(기존 환경변수가 우선). 배포 땐 실제 env 사용."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env_int(names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = os.environ.get(name)
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
    return default


_load_dotenv()
SEOUL_API_KEY = os.environ.get("SEOUL_API_KEY", "")
BASE = "http://openapi.seoul.go.kr:8088"
CACHE_TTL = 300  # 5분: rate limit 회피 + 응답 속도(데이터도 5분 갱신)

SERVER_HOST = os.environ.get("MCP_HOST") or os.environ.get("HOST") or "127.0.0.1"
SERVER_PORT = _env_int(("MCP_PORT", "PORT"), 8000)
MCP_PATH = os.environ.get("MCP_PATH", "/mcp")
if not MCP_PATH.startswith("/"):
    MCP_PATH = "/" + MCP_PATH

mcp = FastMCP(
    "hotspot-congestion",
    host=SERVER_HOST,
    port=SERVER_PORT,
    streamable_http_path=MCP_PATH,
)

# 혼잡도 4단계(낮을수록 한산) — 실제 응답 값과 일치 확인됨
CONGEST_ORDER = {"여유": 0, "보통": 1, "약간 붐빔": 2, "붐빔": 3}

# seoul_areas.json 로드 실패 시 폴백(대표 일부)
SUPPORTED_PLACES = [
    "홍대 관광특구", "강남역", "성수카페거리", "광화문·덕수궁", "명동 관광특구",
    "이태원 관광특구", "여의도한강공원", "잠실종합운동장", "북촌한옥마을", "서울숲공원",
]

# 흔한 약칭/별칭 → 공식 장소명. 동명 애매성("광화문","잠실","홍대" 등) 결정적 해소.
ALIASES = {
    "홍대": "홍대 관광특구", "명동": "명동 관광특구", "이태원": "이태원 관광특구",
    "동대문": "동대문 관광특구", "잠실": "잠실 관광특구", "종로": "종로·청계 관광특구",
    "강남": "강남역", "코엑스": "강남 MICE 관광특구", "삼성역": "강남 MICE 관광특구",
    "성수": "성수카페거리", "성수동": "성수카페거리", "압구정": "압구정로데오거리",
    "청담": "청담동 명품거리", "광화문": "광화문·덕수궁",
    "홍대입구": "홍대입구역(2호선)", "홍대입구역": "홍대입구역(2호선)",
    "연남": "연남동", "연트럴파크": "연남동",
    "롯데타워": "잠실롯데타워·석촌호수", "석촌호수": "잠실롯데타워·석촌호수",
    "롯데월드": "잠실롯데타워·석촌호수", "롯데월드타워": "잠실롯데타워·석촌호수",
    "ddp": "DDP(동대문디자인플라자)", "동대문디자인플라자": "DDP(동대문디자인플라자)",
    "dmc": "DMC(디지털미디어시티)", "남산": "남산공원", "서울숲": "서울숲공원",
    "남산타워": "남산공원", "n서울타워": "남산공원", "서울타워": "남산공원",
    "신촌": "신촌·이대역", "건대": "건대입구역", "서울대": "서울대입구역",
    "여의도한강": "여의도한강공원", "더현대": "여의도", "더현대서울": "여의도",
    "뚝섬유원지": "뚝섬한강공원", "뚝섬한강": "뚝섬한강공원",
    "망원한강": "망원한강공원", "반포한강": "반포한강공원", "난지한강": "난지한강공원",
    "광나루한강": "광나루한강공원", "양화한강": "양화한강공원", "이촌한강": "이촌한강공원",
    "잠실한강": "잠실한강공원", "잠원한강": "잠원한강공원",
    "광장시장": "광장(전통)시장", "남대문": "남대문시장", "타임스퀘어": "영등포 타임스퀘어",
    "고터": "고속터미널역", "강남고터": "고속터미널역", "이수역": "총신대입구(이수)역",
}


def _load_areas() -> list[str]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seoul_areas.json")
    try:
        with open(path, encoding="utf-8") as f:
            names = [d["name"] for d in json.load(f) if d.get("name")]
        if names:
            return names
    except Exception:
        pass
    return SUPPORTED_PLACES


AREAS = _load_areas()


def _has_any(name: str, keywords: tuple[str, ...]) -> bool:
    return any(k in name for k in keywords)


def _area_categories() -> dict[str, list[str]]:
    categories = {
        "관광특구": [],
        "한강공원": [],
        "공원·산책": [],
        "역세권": [],
        "상권·거리·시장": [],
        "고궁·문화·명소": [],
        "기타": [],
    }
    for name in AREAS:
        if name.endswith("관광특구"):
            categories["관광특구"].append(name)
        elif "한강공원" in name:
            categories["한강공원"].append(name)
        elif "공원" in name or "숲" in name or name in ("아차산", "응봉산", "청계산", "노들섬", "안양천", "홍제폭포", "송현녹지광장"):
            categories["공원·산책"].append(name)
        elif name.endswith("역") or "역(" in name or "역·" in name or "·" in name and "역" in name:
            categories["역세권"].append(name)
        elif _has_any(name, ("거리", "길", "시장", "타임스퀘어", "카페", "로데오", "먹자", "서촌", "익선동", "인사동", "여의도", "노량진", "해방촌")):
            categories["상권·거리·시장"].append(name)
        elif _has_any(name, ("궁", "종묘", "박물관", "DDP", "돔", "운동장", "광장", "보신각", "숭례문", "유적", "공항")):
            categories["고궁·문화·명소"].append(name)
        else:
            categories["기타"].append(name)
    return {k: v for k, v in categories.items() if v}


AREA_CATEGORIES = _area_categories()

DEFAULT_RECOMMEND_AREAS = [
    "홍대 관광특구", "홍대입구역(2호선)", "연남동", "성수카페거리", "강남역",
    "잠실롯데타워·석촌호수", "명동 관광특구", "광화문·덕수궁", "이태원 관광특구",
    "여의도", "여의도한강공원", "반포한강공원", "뚝섬한강공원", "서울숲공원",
]

CATEGORY_ALIASES = {
    "전체": "전체", "핫플": "전체", "어디": "전체", "근처": "전체",
    "한강": "한강공원", "한강공원": "한강공원",
    "공원": "공원·산책", "산책": "공원·산책", "숲": "공원·산책", "산": "공원·산책",
    "역": "역세권", "역세권": "역세권", "지하철": "역세권",
    "상권": "상권·거리·시장", "거리": "상권·거리·시장", "시장": "상권·거리·시장", "카페": "상권·거리·시장",
    "관광": "관광특구", "관광특구": "관광특구",
    "문화": "고궁·문화·명소", "고궁": "고궁·문화·명소", "명소": "고궁·문화·명소",
}

_NORM_RE = re.compile(r"[\s·・•()\[\]\-_.,~’'\"]+")


def _norm(s: str) -> str:
    """매칭용 정규화: 공백·가운뎃점·괄호·기호 제거 + 소문자. 질의·공식명·별칭 모두 같은 함수."""
    return _NORM_RE.sub("", s.strip().lower())


_NORM_TO_NAME = {_norm(n): n for n in AREAS}
_NORM_ALIASES = {_norm(k): v for k, v in ALIASES.items()}


def _embedded_place_matches(q: str) -> list[str]:
    """'홍대입구역 롯데리아'처럼 지원 장소명 뒤에 부가어가 붙은 입력을 복구한다."""
    hits: list[tuple[int, str]] = []
    for norm, name in _NORM_TO_NAME.items():
        if len(norm) >= 3 and norm in q:
            hits.append((len(norm), name))
    for norm, name in _NORM_ALIASES.items():
        if len(norm) >= 2 and norm in q:
            hits.append((len(norm), name))
    if not hits:
        return []
    max_len = max(length for length, _ in hits)
    names = []
    for _, name in sorted((h for h in hits if h[0] == max_len), reverse=True):
        if name not in names:
            names.append(name)
    return names


def resolve_place(query: str):
    """유저 입력 → 공식 장소명. 반환: ("ok", 이름) | ("ambiguous", [후보]) | ("notfound", [])."""
    q = _norm(query)
    if not q:
        return ("notfound", [])
    if q in _NORM_ALIASES:                       # 1. 별칭(애매성 우선 해소)
        return ("ok", _NORM_ALIASES[q])
    if q in _NORM_TO_NAME:                        # 2. 정확 일치
        return ("ok", _NORM_TO_NAME[q])
    contains = [name for norm, name in _NORM_TO_NAME.items() if q in norm]  # 3. 부분 일치
    if len(contains) == 1:
        return ("ok", contains[0])
    if len(contains) > 1:
        return ("ambiguous", contains[:6])
    embedded = _embedded_place_matches(q)          # 4. 지원 장소명 + 가게명/부가어 복구
    if len(embedded) == 1:
        return ("ok", embedded[0])
    if len(embedded) > 1:
        return ("ambiguous", embedded[:6])
    close = difflib.get_close_matches(q, list(_NORM_TO_NAME), n=3, cutoff=0.6)  # 5. 오타 후보
    if close:
        # 자동 확정 금지: '판교역'→교대역, '샤로수길'→가로수길처럼 미지원 장소가
        # 비슷한 이름의 엉뚱한 데이터로 답하는 것 방지(유사도로는 진짜 오타와 구분 불가)
        return ("ambiguous", [_NORM_TO_NAME[c] for c in close])
    return ("notfound", [])


# ---------- 데이터 조회 ----------
class CityDataError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


_cache: dict[str, tuple[float, dict]] = {}


async def fetch_citydata(place_name: str, force: bool = False) -> dict[str, Any]:
    """공식 장소명으로 citydata 호출 → CITYDATA dict. 5분 TTL 캐시(성공만). force면 캐시 무시하고 갱신."""
    now = time.time()
    hit = _cache.get(place_name)
    if not force and hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    if not SEOUL_API_KEY:
        raise CityDataError("NO_KEY", "SEOUL_API_KEY 미설정")
    url = f"{BASE}/{SEOUL_API_KEY}/json/citydata/1/1/{urllib.parse.quote(place_name, safe='')}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
        result = data.get("RESULT") or {}
        code = result.get("RESULT.CODE")
        if code and code != "INFO-000":
            raise CityDataError(code, result.get("RESULT.MESSAGE", ""))
        city = data.get("CITYDATA")
        if city is None and any(k in data for k in ("AREA_NM", "LIVE_PPLTN_STTS", "WEATHER_STTS")):
            city = data
        if not isinstance(city, dict) or not city.get("AREA_NM"):
            raise CityDataError("NO_DATA", "CITYDATA 없음")
    except Exception:
        if hit:  # 일시 장애면 직전 성공 데이터로 응답 — 기준시각이 표기되므로 안전
            return hit[1]
        raise
    _cache[place_name] = (now, city)
    return city


def _first(node: Any) -> dict:
    """API가 값을 dict 또는 [dict]로 주는 경우 모두 처리(LIVE_PPLTN_STTS=list, LIVE_CMRCL_STTS=dict)."""
    if isinstance(node, list):
        return node[0] if node else {}
    return node or {}


# ---------- 파싱(T2에서 실제 응답과 필드명 일치 확인) ----------
def parse_congestion(citydata: dict) -> dict:
    ppltn = _first(citydata.get("LIVE_PPLTN_STTS"))
    cmrcl = _first(citydata.get("LIVE_CMRCL_STTS"))
    return {
        "area": citydata.get("AREA_NM", ""),
        "congest_level": ppltn.get("AREA_CONGEST_LVL") or "정보없음",
        "congest_msg": ppltn.get("AREA_CONGEST_MSG", ""),
        "ppltn_min": ppltn.get("AREA_PPLTN_MIN", ""),
        "ppltn_max": ppltn.get("AREA_PPLTN_MAX", ""),
        "commercial_level": cmrcl.get("AREA_CMRCL_LVL") or "정보없음",
        "ppltn_time": ppltn.get("PPLTN_TIME", ""),
    }


def parse_forecast(citydata: dict) -> list[dict]:
    ppltn = _first(citydata.get("LIVE_PPLTN_STTS"))
    fcst = ppltn.get("FCST_PPLTN") or []
    if isinstance(fcst, dict):
        fcst = [fcst]
    return [{
        "time": f.get("FCST_TIME", ""),
        "level": f.get("FCST_CONGEST_LVL", ""),
        "ppltn_min": f.get("FCST_PPLTN_MIN", ""),
        "ppltn_max": f.get("FCST_PPLTN_MAX", ""),
    } for f in fcst]


def _to_int(value: Any, default: int = 999999999) -> int:
    digits = re.sub(r"\D+", "", str(value or ""))
    return int(digits) if digits else default


def _population_text(c: dict) -> str:
    mn, mx = c.get("ppltn_min"), c.get("ppltn_max")
    if mn and mx:
        return f"실시간 인구 {mn}~{mx}명"
    return "실시간 인구 정보없음"


def _weather_by_hour(citydata: dict) -> dict[str, dict]:
    weather = _first(citydata.get("WEATHER_STTS"))
    fcst = weather.get("FCST24HOURS") or []
    if isinstance(fcst, dict):
        fcst = [fcst]
    out = {}
    for item in fcst:
        dt = str(item.get("FCST_DT", ""))
        if len(dt) >= 10:
            out[f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[8:10]}:00"] = item
    return out


def _weather_suffix(time_text: str, weather_map: dict[str, dict]) -> str:
    item = weather_map.get(time_text)
    if not item:
        return ""
    parts = []
    if item.get("TEMP"):
        parts.append(f"{item['TEMP']}°C")
    if item.get("SKY_STTS"):
        parts.append(item["SKY_STTS"])
    if item.get("RAIN_CHANCE"):
        parts.append(f"강수확률 {item['RAIN_CHANCE']}%")
    ptype = item.get("PRECPT_TYPE")
    if ptype and ptype != "없음":
        parts.append(ptype)
    return f" ({', '.join(parts)})" if parts else ""


def _visit_advisories(citydata: dict) -> list[str]:
    notes = []
    weather = _first(citydata.get("WEATHER_STTS"))
    if weather:
        ptype = weather.get("PRECPT_TYPE")
        if ptype and ptype != "없음":
            notes.append(f"현재 {ptype} 소식이 있어요")
        elif weather.get("PCP_MSG") and "없" not in weather["PCP_MSG"]:
            notes.append(weather["PCP_MSG"])
        if weather.get("PM10_INDEX") in ("나쁨", "매우나쁨") or weather.get("PM25_INDEX") in ("나쁨", "매우나쁨"):
            notes.append("미세먼지 상태를 확인하고 이동하세요")

    road_root = citydata.get("ROAD_TRAFFIC_STTS") or {}
    road = _first(road_root.get("AVG_ROAD_DATA") if isinstance(road_root, dict) else {})
    if road.get("ROAD_TRAFFIC_IDX") in ("정체", "서행"):
        notes.append(f"주변 도로 {road['ROAD_TRAFFIC_IDX']}")

    for section in ("LIVE_DST_MESSAGE", "ACDNT_CNTRL_STTS"):
        items = citydata.get(section) or []
        if isinstance(items, dict):
            items = [items]
        if items:
            notes.append("재난·통제 정보가 있어요")
            break
    return notes[:2]


def pick_best_times(forecast: list[dict], top_n: int = 3) -> list[dict]:
    ranked = sorted(
        [f for f in forecast if f.get("level") in CONGEST_ORDER],
        key=lambda f: (CONGEST_ORDER[f["level"]], f.get("time", "")),
    )
    return ranked[:top_n]


NOTE = "※ 서울시 실시간 도시데이터 기반 · 통신사 기지국 추정치라 실제와 차이가 있을 수 있어요."


def _category_key(category: str) -> str:
    q = _norm(category or "전체")
    if not q:
        return "전체"
    # '근처·어디' 같은 포괄 별칭이 '한강공원 근처'의 '한강'을 삼키지 않도록: 구체 카테고리 최장일치 우선
    specific = [(len(_norm(a)), k) for a, k in CATEGORY_ALIASES.items()
                if k != "전체" and _norm(a) in q]
    return max(specific)[1] if specific else "전체"


def _candidate_places(category: str) -> tuple[str, list[str]]:
    key = _category_key(category)
    if key == "전체":
        return key, list(AREAS)
    return key, AREA_CATEGORIES.get(key, [])


async def _fetch_summary(place: str) -> dict:
    data = await fetch_citydata(place)
    c = parse_congestion(data)
    return {
        "place": c["area"] or place,
        "level": c["congest_level"],
        "score": CONGEST_ORDER.get(c["congest_level"], 99),
        "population_score": (_to_int(c.get("ppltn_min")) + _to_int(c.get("ppltn_max"))) / 2,
        "population": _population_text(c),
        "commercial": c["commercial_level"],
        "ppltn_time": c["ppltn_time"],
        "advisories": _visit_advisories(data),
    }


async def _fetch_many_summaries(places: list[str]) -> tuple[list[dict], list[tuple[str, Exception]]]:
    results = await asyncio.gather(*(_fetch_summary(p) for p in places), return_exceptions=True)
    ok, errors = [], []
    for place, result in zip(places, results):
        if isinstance(result, Exception):
            errors.append((place, result))
        else:
            ok.append(result)
    ok.sort(key=lambda x: (x["score"], x["population_score"], x["place"]))
    return ok, errors


def _unresolved_msg(query: str, kind: str, candidates: list[str]) -> str:
    if kind == "ambiguous":
        if len(candidates) == 1:
            return (f"'{query}'을(를) 정확히 찾지 못했어요. 혹시 '{candidates[0]}' 말씀이신가요? "
                    f"맞으면 그 이름으로 다시 물어봐 주세요. (지원하지 않는 장소일 수도 있어요)")
        return (f"'{query}' 후보가 여러 곳이에요: {', '.join(candidates)}\n"
                f"정확한 이름으로 다시 물어봐 주세요.")
    return (f"'{query}'은(는) 지원 목록에 없어요. "
            f"'성수'·'홍대'·'코엑스'처럼 말하거나 list_supported_hotspots로 확인해 주세요.")


def _friendly_error(e: Exception, place: str) -> str:
    if isinstance(e, CityDataError):
        if e.code == "INFO-200":
            return f"'{place}'의 실시간 데이터가 지금은 비어 있어요. 잠시 후 다시 시도해 주세요."
        if e.code == "NO_KEY":
            return "서버에 서울 API 키(SEOUL_API_KEY)가 설정되지 않았어요."
        if e.code == "ERROR-337":
            return f"'{place}' 조회 요청이 잠시 많아요. 잠시 후 다시 시도해 주세요."
        return f"'{place}' 조회 중 오류가 났어요. 잠시 후 다시 시도해 주세요."
    return f"'{place}' 조회에 실패했어요(일시적 오류). 잠시 후 다시 시도해 주세요."


# ---------- MCP 도구 ----------
def _annot(role: str, open_world: bool = True) -> ToolAnnotations:
    """PlayMCP 심사 요건: 툴별 annotations 필수. 전 도구 읽기 전용(조회만)."""
    return ToolAnnotations(
        title=f"핫플 혼잡도 비서 · {role}",
        readOnlyHint=True,
        openWorldHint=open_world,
    )


@mcp.tool(annotations=_annot("현재 혼잡도 조회"))
async def get_hotspot_congestion(place_name: str) -> str:
    """[핫플 혼잡도 비서] 서울 주요 장소의 지금 혼잡도와 상권 활기를 알려줍니다. 가게명보다 '홍대입구역', '성수', '더현대'처럼 장소/권역명을 넣으세요."""
    kind, val = resolve_place(place_name)
    if kind != "ok":
        return _unresolved_msg(place_name, kind, val)
    place = val
    try:
        data = await fetch_citydata(place)
    except Exception as e:
        return _friendly_error(e, place)
    c = parse_congestion(data)
    advisories = _visit_advisories(data)
    advisory_line = f"방문 참고: {' / '.join(advisories)}\n" if advisories else ""
    basis = f"{c['ppltn_time']} 기준" if c["ppltn_time"] else "지금"
    return (
        f"📍 {c['area'] or place} ({basis})\n"
        f"혼잡도: {c['congest_level']} ({_population_text(c)})\n"
        f"상권 활기: {c['commercial_level']}\n"
        f"한줄: {c['congest_msg']}\n"
        f"{advisory_line}"
        f"{NOTE}"
    )


@mcp.tool(annotations=_annot("혼잡도 비교(2~4곳)"))
async def compare_hotspots(place_a: str, place_b: str, place_c: str = "", place_d: str = "") -> str:
    """[핫플 혼잡도 비서] 서울 핫플 2~4곳의 현재 혼잡도를 비교합니다. '성수 vs 홍대 vs 강남역'처럼 여러 후보 중 더 한산한 곳을 고를 때 쓰세요."""
    queries = [q.strip() for q in (place_a, place_b, place_c, place_d) if q and q.strip()]
    if len(queries) < 2:
        return "비교하려면 장소를 최소 2곳 알려주세요. 예: 성수와 홍대 비교"

    places = []
    for query in queries:
        kind, val = resolve_place(query)
        if kind != "ok":
            return _unresolved_msg(query, kind, val)
        if val not in places:
            places.append(val)

    if len(places) < 2:  # 별칭 둘이 같은 장소로 좁혀지면 비교가 성립 안 함
        return f"입력하신 곳이 모두 '{places[0]}' 한 곳이에요. 서로 다른 두 곳을 비교해 주세요."

    summaries, errors = await _fetch_many_summaries(places)
    if errors and not summaries:
        place, err = errors[0]
        return _friendly_error(err, place)
    lines = [
        f"{i}. {s['place']}: {s['level']} ({s['population']})"
        for i, s in enumerate(summaries, 1)
    ]
    if len(summaries) >= 2 and summaries[0]["score"] == summaries[1]["score"]:
        tied = [s["place"] for s in summaries if s["score"] == summaries[0]["score"]]
        verdict = f"👉 지금 가장 한산한 후보는 {', '.join(tied[:3])}로 비슷해요."
    else:
        verdict = f"👉 지금 가장 한산한 곳은 '{summaries[0]['place']}'이에요."
    if errors:
        lines.append("일부 장소는 일시적으로 조회하지 못했어요: " + ", ".join(p for p, _ in errors))
    basis = next((s["ppltn_time"] for s in summaries if s.get("ppltn_time")), "")
    tail = [verdict, f"({basis} 기준)"] if basis else [verdict]
    return "\n".join(lines + tail + [NOTE])


@mcp.tool(annotations=_annot("한산한 시간대 추천"))
async def best_time_to_go(place_name: str) -> str:
    """[핫플 혼잡도 비서] 서울 주요 장소의 향후 12시간 혼잡도 예측에서 가장 한산한 방문 시간대를 추천합니다."""
    kind, val = resolve_place(place_name)
    if kind != "ok":
        return _unresolved_msg(place_name, kind, val)
    place = val
    try:
        data = await fetch_citydata(place)
    except Exception as e:
        return _friendly_error(e, place)
    best = pick_best_times(parse_forecast(data))
    if not best:
        return f"'{place}'의 예측 데이터가 지금은 없어요.\n{NOTE}"
    weather_map = _weather_by_hour(data)
    lines = [f"- {b['time']}: {b['level']}{_weather_suffix(b['time'], weather_map)}" for b in best]
    if CONGEST_ORDER.get(best[0]["level"], 0) >= CONGEST_ORDER["약간 붐빔"]:
        header = f"⏰ '{place}'은(는) 앞으로 12시간 내내 붐비는 편이에요. 그나마 나은 시간대 TOP {len(best)}"
    else:
        header = f"⏰ '{place}' 앞으로 12시간 중 한산한 시간대 TOP {len(best)}"
    return header + "\n" + "\n".join(lines) + f"\n{NOTE}"


@mcp.tool(annotations=_annot("지원 장소 목록", open_world=False))
def list_supported_hotspots() -> str:
    """[핫플 혼잡도 비서] 혼잡도 조회가 가능한 서울 주요 121곳 전체 목록을 카테고리별로 안내합니다. 장소명을 모를 때 먼저 쓰세요."""
    total = len(AREAS)
    examples = ["강남역", "홍대 관광특구", "성수카페거리", "여의도한강공원",
                "광화문·덕수궁", "명동 관광특구", "북촌한옥마을", "잠실롯데타워·석촌호수"]
    lines = [
        f"서울 주요 {total}곳의 실시간 혼잡도·12시간 예측을 알려드려요.\n"
        f"예: {', '.join(examples)}\n"
        f"정확한 이름을 몰라도 '성수'·'홍대입구역 롯데리아'·'더현대'처럼 말하면 가까운 지원 권역으로 찾아드려요."
    ]
    for category, names in AREA_CATEGORIES.items():
        lines.append(f"\n[{category}] {len(names)}곳\n" + ", ".join(names))
    return "\n".join(lines)


@mcp.tool(annotations=_annot("한산한 곳 추천"))
async def recommend_less_crowded_hotspots(category: str = "전체", limit: int = 5) -> str:
    """[핫플 혼잡도 비서] 지금 바로 갈 만한 한산한 서울 핫플을 추천합니다. category는 전체, 한강공원, 공원, 역세권, 상권, 관광특구, 고궁, 명소처럼 넣으세요."""
    key, candidates = _candidate_places(category)
    if not candidates:
        return f"'{category}'에 맞는 추천 후보를 찾지 못했어요. list_supported_hotspots로 지원 장소를 확인해 주세요."
    limit = max(1, min(int(limit or 5), 8))
    summaries, errors = await _fetch_many_summaries(candidates)
    if not summaries:
        place, err = errors[0]
        return _friendly_error(err, place)
    top = summaries[:limit]
    lines = []
    for i, s in enumerate(top, 1):
        extra = f" / {' / '.join(s['advisories'])}" if s["advisories"] else ""
        lines.append(f"{i}. {s['place']}: {s['level']} ({s['population']}, 상권 {s['commercial']}){extra}")
    scope = f"{key} {len(candidates)}곳 중"
    if errors:
        scope += f", {len(errors)}곳 일시 실패"
    basis = next((s["ppltn_time"] for s in summaries if s.get("ppltn_time")), "")
    basis_line = f" · {basis} 기준" if basis else ""
    return (
        f"📍 지금 비교적 한산한 곳 추천 ({scope}{basis_line})\n"
        + "\n".join(lines)
        + f"\n👉 지금은 '{top[0]['place']}'부터 고려해 보세요.\n{NOTE}"
    )


WARM_SLEEP = 180  # 한 바퀴(약 40초) 돈 뒤 대기 — 데이터 5분 주기보다 촘촘히 갱신


async def _cache_warmer() -> None:
    """백그라운드: 121곳을 미리 당겨 캐시를 채워둠. recommend/조회가 서울 서버를 안 타고 즉답."""
    while True:
        for place in list(AREAS):
            try:
                await fetch_citydata(place, force=True)
            except Exception:
                pass  # 개별 실패는 다음 바퀴에 재시도
        await asyncio.sleep(WARM_SLEEP)


if __name__ == "__main__":
    # 캐시 워머를 별도 스레드(독립 이벤트루프)로 — 서버 이벤트루프와 무관하게 _cache만 채움
    threading.Thread(target=lambda: asyncio.run(_cache_warmer()), daemon=True).start()
    # 원격 MCP: streamable-http (배포 전송은 공식 가이드에 맞춰 확정)
    mcp.run(transport="streamable-http")
