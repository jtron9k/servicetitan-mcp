FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY servicetitan_mcp/ servicetitan_mcp/
COPY README.md .

RUN pip install --no-cache-dir -e .

EXPOSE 8000

ENV MCP_TRANSPORT=sse
CMD python -c "import os; from servicetitan_mcp.server import mcp; t=os.environ.get('MCP_TRANSPORT','stdio'); mcp.run(transport=t, host='0.0.0.0', port=int(os.environ.get('PORT','8000'))) if t=='sse' else mcp.run()"
