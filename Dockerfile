FROM python:3.12-slim

# PlayMCP(카카오클라우드)에 환경변수 주입 UI가 없어 빌드 시 굽는다 — 이미지는 반드시 비공개 유지
ARG SEOUL_API_KEY=""
ENV SEOUL_API_KEY=$SEOUL_API_KEY

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV MCP_HOST=0.0.0.0
ENV PORT=8000
ENV MCP_PATH=/mcp

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hotspot_mcp.py seoul_areas.json ./

EXPOSE 8000

CMD ["python", "hotspot_mcp.py"]
