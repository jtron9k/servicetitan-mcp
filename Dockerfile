FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY servicetitan_mcp/ servicetitan_mcp/
COPY README.md .

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["python", "-m", "servicetitan_mcp.server"]
