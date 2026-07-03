"""실제 서울 API로 MCP 도구 스모크 + resolve/빈상권 진단. venv로 실행."""
import sys
import asyncio

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import hotspot_mcp as H


async def main():
    print("tool type:", type(H.get_hotspot_congestion).__name__,
          "| KEY set:", bool(H.SEOUL_API_KEY), "| areas:", len(H.AREAS))
    for q in ["광화문", "강남역", "성수", "DDP(동대문디자인플라자)", "여의도한강공원"]:
        print("=" * 55)
        print(f"[혼잡도] 입력: {q}")
        print(await H.get_hotspot_congestion(q))
    print("=" * 55); print("[비교] 성수 vs 홍대 vs 강남역")
    print(await H.compare_hotspots("성수", "홍대", "강남역"))
    print("=" * 55); print("[한산시간] 강남역")
    print(await H.best_time_to_go("강남역"))
    print("=" * 55); print("[지금 추천] 한강공원")
    print(await H.recommend_less_crowded_hotspots("한강공원", 3))
    print("=" * 55); print("[목록]")
    print(H.list_supported_hotspots())
    print("=" * 55); print("[resolve 검증]")
    for q in ["광화문", "홍대", "잠실", "여의도", "이수역", "강남역", "남산타워", "더현대", "홍대입구역 롯데리아", "없는곳xyz"]:
        print(f"  {q!r} -> {H.resolve_place(q)}")
    print("=" * 55); print("[빈상권 진단 — 공원이 실제로 상권 없는지]")
    for park in ["남산공원", "여의도한강공원", "북서울꿈의숲"]:
        try:
            data = await H.fetch_citydata(park)
            cm = data.get("LIVE_CMRCL_STTS")
            print(f"  {park}: 상권 present={bool(cm)} -> {H.parse_congestion(data)['commercial_level']!r}")
        except Exception as e:
            print(f"  {park}: ERROR {type(e).__name__} {e}")


if __name__ == "__main__":
    asyncio.run(main())
