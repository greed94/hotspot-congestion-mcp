"""서울 실시간 도시데이터의 지원 장소 목록을 API에서 직접 생성한다(권위 목록).
POI코드(POI001..)를 순회하며 citydata_ppltn(경량)으로 정확한 AREA_NM을 수집.
결과: seoul_areas.json (code/name/fcst_yn/congest). 재생성 가능한 1회성 유틸."""
import json
import urllib.parse
import urllib.request

BASE = "http://openapi.seoul.go.kr:8088"


def read_key(path=".env", key="SEOUL_API_KEY"):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


KEY = read_key()


def fetch_ppltn(area):
    u = f"{BASE}/{KEY}/json/citydata_ppltn/1/5/{urllib.parse.quote(area)}"
    with urllib.request.urlopen(u, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


areas = []
levels = set()
missing = []
for i in range(1, 141):
    code = f"POI{i:03d}"
    try:
        d = fetch_ppltn(code)
    except Exception as e:
        missing.append((code, type(e).__name__))
        continue
    rc = (d.get("RESULT") or {}).get("RESULT.CODE")
    body = d.get("SeoulRtd.citydata_ppltn")
    rec = body[0] if isinstance(body, list) and body else (body if isinstance(body, dict) else {})
    nm = rec.get("AREA_NM")
    if rc == "INFO-000" and nm:
        lvl = rec.get("AREA_CONGEST_LVL")
        areas.append({
            "code": rec.get("AREA_CD") or code,
            "name": nm,
            "fcst_yn": rec.get("FCST_YN"),
            "congest": lvl,
        })
        if lvl:
            levels.add(lvl)
    else:
        missing.append((code, rc))

json.dump(areas, open("seoul_areas.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("valid:", len(areas))
print("levels:", json.dumps(sorted(levels), ensure_ascii=True))
print("fcst_yn distinct:", sorted(set(a["fcst_yn"] for a in areas)))
nof = [a["name"] for a in areas if a["fcst_yn"] != "Y"]
print("no-forecast:", len(nof), json.dumps(nof, ensure_ascii=True))
print("missing/end count:", len(missing), "first:", missing[:3])
