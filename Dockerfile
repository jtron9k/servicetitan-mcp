FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY servicetitan_mcp/ servicetitan_mcp/
COPY README.md .

RUN pip install --no-cache-dir -e .

EXPOSE 8000

ENV MCP_TRANSPORT=sse
ENV HOST=0.0.0.0
CMD python -c "import os; from servicetitan_mcp.server import mcp; mcp.run(transport=os.environ.get('MCP_TRANSPORT','stdio'))"
