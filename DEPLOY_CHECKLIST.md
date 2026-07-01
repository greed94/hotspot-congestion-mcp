# 배포 및 제출 체크리스트

## 로컬 확인

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe test_hotspot.py
.\.venv\Scripts\python.exe smoke_test.py
```

## 카카오클라우드 환경변수

- `SEOUL_API_KEY`: 서울 열린데이터광장 API 키
- `MCP_HOST`: `0.0.0.0`
- `PORT`: 카카오클라우드가 지정한 포트. 별도 지정이 없으면 `8000`
- `MCP_PATH`: `/mcp`

## 실행 명령

```bash
python hotspot_mcp.py
```

배포 후 MCP Endpoint는 보통 다음 형태입니다.

```text
https://<배포된-도메인>/mcp
```

## 예선 제출 흐름

1. 카카오클라우드에서 MCP 서버 Endpoint 생성
2. PlayMCP 개발자 콘솔에서 Endpoint로 서버 등록
3. 먼저 `임시 등록`으로 AI 채팅 호출 테스트
4. 최종 버전이면 `등록 및 심사 요청`
5. 심사 승인 후 공개 상태를 `전체 공개`로 변경
6. 공모전 페이지에서 `[Player 예선 참여]` 제출

주의: 배포만으로는 예선 제출이 끝나지 않습니다. PlayMCP 등록, 심사 요청, 승인 후 전체 공개, Player 예선 참여 제출까지 해야 합니다.
