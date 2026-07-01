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
import difflib
import urllib.parse
from typing import Any

import logging

import httpx
from mcp.server.fastmcp import FastMCP

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
    "롯데타워": "잠실롯데타워·석촌호수", "석촌호수": "잠실롯데타워·석촌호수",
    "ddp": "DDP(동대문디자인플라자)", "동대문디자인플라자": "DDP(동대문디자인플라자)",
    "dmc": "DMC(디지털미디어시티)", "남산": "남산공원", "서울숲": "서울숲공원",
    "신촌": "신촌·이대역", "건대": "건대입구역", "서울대": "서울대입구역",
    "여의도한강": "여의도한강공원",
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

_NORM_RE = re.compile(r"[\s·・•()\[\]\-_.,~’'\"]+")


def _norm(s: str) -> str:
    """매칭용 정규화: 공백·가운뎃점·괄호·기호 제거 + 소문자. 질의·공식명·별칭 모두 같은 함수."""
    return _NORM_RE.sub("", s.strip().lower())


_NORM_TO_NAME = {_norm(n): n for n in AREAS}
_NORM_ALIASES = {_norm(k): v for k, v in ALIASES.items()}


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
    close = difflib.get_close_matches(q, list(_NORM_TO_NAME), n=3, cutoff=0.6)  # 4. 오타 보정
    if len(close) == 1:
        return ("ok", _NORM_TO_NAME[close[0]])
    if close:
        return ("ambiguous", [_NORM_TO_NAME[c] for c in close])
    return ("notfound", [])


# ---------- 데이터 조회 ----------
class CityDataError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


_cache: dict[str, tuple[float, dict]] = {}


async def fetch_citydata(place_name: str) -> dict[str, Any]:
    """공식 장소명으로 citydata 호출 → CITYDATA dict. 5분 TTL 캐시(성공 INFO-000만 캐시)."""
    now = time.time()
    hit = _cache.get(place_name)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    if not SEOUL_API_KEY:
        raise CityDataError("NO_KEY", "SEOUL_API_KEY 미설정")
    url = f"{BASE}/{SEOUL_API_KEY}/json/citydata/1/1/{urllib.parse.quote(place_name, safe='')}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    result = data.get("RESULT") or {}
    code = result.get("RESULT.CODE")
    if code and code != "INFO-000":
        raise CityDataError(code, result.get("RESULT.MESSAGE", ""))
    city = data.get("CITYDATA", data)
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


def pick_best_times(forecast: list[dict], top_n: int = 3) -> list[dict]:
    ranked = sorted(
        [f for f in forecast if f.get("level") in CONGEST_ORDER],
        key=lambda f: (CONGEST_ORDER[f["level"]], f.get("time", "")),
    )
    return ranked[:top_n]


NOTE = "※ 통신사 기지국 기반 추정치라 실제와 차이가 있을 수 있어요."


def _unresolved_msg(query: str, kind: str, candidates: list[str]) -> str:
    if kind == "ambiguous":
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
        return f"'{place}' 조회 중 오류가 났어요({e.code}). 잠시 후 다시 시도해 주세요."
    return f"'{place}' 조회에 실패했어요(일시적 오류). 잠시 후 다시 시도해 주세요."


# ---------- MCP 도구 ----------
@mcp.tool()
async def get_hotspot_congestion(place_name: str) -> str:
    """지정한 서울 핫플의 지금 혼잡도와 상권 활기를 알려줍니다."""
    kind, val = resolve_place(place_name)
    if kind != "ok":
        return _unresolved_msg(place_name, kind, val)
    place = val
    try:
        data = await fetch_citydata(place)
    except Exception as e:
        return _friendly_error(e, place)
    c = parse_congestion(data)
    return (
        f"📍 {c['area'] or place} 지금\n"
        f"혼잡도: {c['congest_level']} (실시간 인구 {c['ppltn_min']}~{c['ppltn_max']}명)\n"
        f"상권 활기: {c['commercial_level']}\n"
        f"한줄: {c['congest_msg']}\n"
        f"{NOTE}"
    )


@mcp.tool()
async def compare_hotspots(place_a: str, place_b: str) -> str:
    """두 핫플의 현재 혼잡도를 비교해 어디가 더 한산한지 알려줍니다."""
    ka, va = resolve_place(place_a)
    if ka != "ok":
        return _unresolved_msg(place_a, ka, va)
    kb, vb = resolve_place(place_b)
    if kb != "ok":
        return _unresolved_msg(place_b, kb, vb)
    a, b = va, vb
    try:
        da = await fetch_citydata(a)
    except Exception as e:
        return _friendly_error(e, a)
    try:
        db = await fetch_citydata(b)
    except Exception as e:
        return _friendly_error(e, b)
    ca, cb = parse_congestion(da), parse_congestion(db)
    la = CONGEST_ORDER.get(ca["congest_level"], 99)
    lb = CONGEST_ORDER.get(cb["congest_level"], 99)
    if la == lb:
        verdict = "둘 다 비슷해요."
    else:
        verdict = f"👉 지금은 '{a if la < lb else b}'가 더 한산해요."
    return (
        f"{a}: {ca['congest_level']}\n"
        f"{b}: {cb['congest_level']}\n{verdict}\n{NOTE}"
    )


@mcp.tool()
async def best_time_to_go(place_name: str) -> str:
    """향후 12시간 예측으로 가장 한산한 방문 시간대를 추천합니다."""
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
    lines = [f"- {b['time']}: {b['level']}" for b in best]
    return (f"⏰ '{place}' 앞으로 12시간 중 한산한 시간대 TOP {len(best)}\n"
            + "\n".join(lines) + f"\n{NOTE}")


@mcp.tool()
def list_supported_hotspots() -> str:
    """혼잡도 조회가 가능한 서울 주요 장소 목록을 안내합니다."""
    total = len(AREAS)
    tourist = [n for n in AREAS if n.endswith("관광특구")]
    parks = [n for n in AREAS if any(k in n for k in ("공원", "한강", "산", "숲", "섬"))]
    streets = [n for n in AREAS if any(k in n for k in ("거리", "길", "로데오", "단길"))]
    examples = ["강남역", "홍대 관광특구", "성수카페거리", "여의도한강공원",
                "광화문·덕수궁", "명동 관광특구", "북촌한옥마을", "잠실롯데타워·석촌호수"]
    return (
        f"서울 주요 {total}곳의 실시간 혼잡도·12시간 예측을 알려드려요.\n"
        f"· 관광특구 {len(tourist)}곳 (홍대·명동·강남 MICE 등)\n"
        f"· 공원·한강·산 {len(parks)}곳\n"
        f"· 상권·거리 {len(streets)}곳, 그 외 주요 역·고궁·시장\n"
        f"예: {', '.join(examples)}\n"
        f"정확한 이름을 몰라도 '성수'·'홍대'·'코엑스'처럼 말하면 찾아드려요."
    )


if __name__ == "__main__":
    # 원격 MCP: streamable-http (배포 전송은 공식 가이드에 맞춰 확정)
    mcp.run(transport="streamable-http")
