FROM python:3.12-slim

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
