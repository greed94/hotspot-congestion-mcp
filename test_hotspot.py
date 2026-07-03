"""핫플 혼잡도 MVP 로직 검증 (합성 데이터). 실제 서울 API는 이 환경에서 접근 불가 → 벌새 로직만 검증."""
import asyncio
import hotspot_mcp as H

# 서울 citydata 응답을 모사한 합성 샘플 (문서화된 필드 구조 기준)
SAMPLE = {
    "CITYDATA": {
        "AREA_NM": "성수카페거리",
        "LIVE_PPLTN_STTS": [{
            "AREA_CONGEST_LVL": "약간 붐빔",
            "AREA_CONGEST_MSG": "사람이 몰리기 시작했어요. 이동에 조금 불편할 수 있어요.",
            "AREA_PPLTN_MIN": "12000", "AREA_PPLTN_MAX": "14000",
            "FCST_PPLTN": [
                {"FCST_TIME": "2026-07-01 15:00", "FCST_CONGEST_LVL": "붐빔",     "FCST_PPLTN_MIN": "15000", "FCST_PPLTN_MAX": "17000"},
                {"FCST_TIME": "2026-07-01 17:00", "FCST_CONGEST_LVL": "약간 붐빔", "FCST_PPLTN_MIN": "11000", "FCST_PPLTN_MAX": "13000"},
                {"FCST_TIME": "2026-07-01 21:00", "FCST_CONGEST_LVL": "여유",     "FCST_PPLTN_MIN": "3000",  "FCST_PPLTN_MAX": "5000"},
                {"FCST_TIME": "2026-07-01 19:00", "FCST_CONGEST_LVL": "보통",     "FCST_PPLTN_MIN": "7000",  "FCST_PPLTN_MAX": "9000"},
            ],
        }],
        "LIVE_CMRCL_STTS": [{"AREA_CMRCL_LVL": "바쁨"}],
    }
}
# 비교용 두 번째 장소(여유)
SAMPLE_B = {"CITYDATA": {"AREA_NM": "서울숲공원",
    "LIVE_PPLTN_STTS": {"AREA_CONGEST_LVL": "여유", "AREA_CONGEST_MSG": "한산", "AREA_PPLTN_MIN": "1000", "AREA_PPLTN_MAX": "2000"},
    "LIVE_CMRCL_STTS": {"AREA_CMRCL_LVL": "한산"}}}  # dict 형태(list 아님)도 처리되는지 확인


def test_parse_congestion():
    c = H.parse_congestion(SAMPLE["CITYDATA"])
    assert c["area"] == "성수카페거리"
    assert c["congest_level"] == "약간 붐빔"
    assert c["commercial_level"] == "바쁨"
    assert c["ppltn_max"] == "14000"
    print("✓ parse_congestion (list 형태)")

    # dict 형태도 처리되는지
    c2 = H.parse_congestion(SAMPLE_B["CITYDATA"])
    assert c2["congest_level"] == "여유" and c2["commercial_level"] == "한산"
    print("✓ parse_congestion (dict 형태)")


def test_parse_forecast():
    f = H.parse_forecast(SAMPLE["CITYDATA"])
    assert len(f) == 4
    assert f[0]["level"] == "붐빔" and f[0]["time"] == "2026-07-01 15:00"
    print(f"✓ parse_forecast ({len(f)}개 시간대 추출)")


def test_pick_best_times():
    best = H.pick_best_times(H.parse_forecast(SAMPLE["CITYDATA"]), top_n=3)
    levels = [b["level"] for b in best]
    # 가장 한산한 순: 여유(21시) → 보통(19시) → 약간붐빔(17시)
    assert levels == ["여유", "보통", "약간 붐빔"], levels
    assert best[0]["time"] == "2026-07-01 21:00"
    print(f"✓ pick_best_times → 추천: {[(b['time'][-5:], b['level']) for b in best]}")


def test_compare_logic():
    la = H.CONGEST_ORDER["약간 붐빔"]; lb = H.CONGEST_ORDER["여유"]
    assert lb < la  # 여유가 더 한산 → 서울숲이 이겨야
    print("✓ compare 판정 로직(여유 < 약간 붐빔)")


def test_resolve_place_fallbacks():
    assert H.resolve_place("홍대입구역 롯데리아") == ("ok", "홍대입구역(2호선)")
    assert H.resolve_place("남산타워") == ("ok", "남산공원")
    assert H.resolve_place("더현대서울") == ("ok", "여의도")
    assert H.resolve_place("뚝섬유원지 근처") == ("ok", "뚝섬한강공원")
    print("✓ resolve_place 별칭/부가어 폴백")


def test_population_text_guard():
    assert H._population_text({"ppltn_min": "", "ppltn_max": ""}) == "실시간 인구 정보없음"
    assert H._population_text({"ppltn_min": "1000", "ppltn_max": "2000"}) == "실시간 인구 1000~2000명"
    print("✓ 인구 범위 빈값 출력 가드")


async def test_tools_registered():
    tools = await H.mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "get_hotspot_congestion",
        "compare_hotspots",
        "best_time_to_go",
        "list_supported_hotspots",
        "recommend_less_crowded_hotspots",
    }
    assert expected.issubset(names), f"누락: {expected - names}"
    print(f"✓ MCP 도구 {len(names)}개 등록 확인: {sorted(names)}")


if __name__ == "__main__":
    test_parse_congestion()
    test_parse_forecast()
    test_pick_best_times()
    test_compare_logic()
    test_resolve_place_fallbacks()
    test_population_text_guard()
    asyncio.run(test_tools_registered())
    print("\n🎉 전 항목 통과 — 파싱/비교/예측 로직 + MCP 도구 등록 정상")
