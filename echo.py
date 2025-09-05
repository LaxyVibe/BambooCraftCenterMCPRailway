import httpx
import logging
import asyncio
import websockets
import subprocess
import os
import signal
import sys
from fastmcp import FastMCP, Context
from pydantic import ValidationError

# --- Logging ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- HTTP client (token via env) ---
api_client = httpx.AsyncClient(
    base_url="https://mfitixkd24e2jo7updj4rtpn.agents.do-ai.run",
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.getenv('API_TOKEN', 'changeme')}"
    },
    timeout=30.0
)

# --- MCP server (stdio) ---
mcp = FastMCP(name="BambooCraftCenter")

@mcp.tool()
async def get_bamboo_craft_center_info(
    messages: list,
    stream: bool = False,
    temperature: float = 0.7,
    top_p: float = 0.9,
    max_tokens: int = 256,
    max_completion_tokens: int = 256,
    k: int = 1,
    retrieval_method: str = "none",
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    ctx: Context = None,
    system_args: dict = None
) -> dict:
    logger.debug(f"Received messages: {messages}, context: {ctx}, system_args: {system_args}")

    if not isinstance(messages, list) or not all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in messages):
        logger.error("Invalid messages format")
        return {"error": "Invalid messages format: Must be a list of dicts with 'role' and 'content'."}

    payload = {
        "messages": messages,
        "stream": stream,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "max_completion_tokens": max_completion_tokens,
        "k": k,
        "retrieval_method": retrieval_method,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
        "include_functions_info": False,
        "include_retrieval_info": False,
        "include_guardrails_info": False,
        "provide_citations": False,
        "filter_kb_content_by_query_metadata": False
    }

    try:
        response = await api_client.post("/api/v1/chat/completions", json=payload)
        response.raise_for_status()
        logger.debug(f"API response: {response.json()}")
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
        return {"error": f"HTTP error {e.response.status_code}: {e.response.text}"}
    except httpx.RequestError as e:
        logger.error(f"Request error: {e}")
        return {"error": str(e)}
    except ValidationError as e:
        logger.error(f"Pydantic validation error: {str(e)}")
        return {"error": f"Pydantic validation error: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {"error": f"Unexpected error: {str(e)}"}

async def connect_with_retry(uri):
    """Connect to WebSocket server with retry/backoff. Spawn stdio MCP server as a child."""
    reconnect_attempt = 0
    backoff = 1
    max_backoff = 600
    process = None

    while True:
        try:
            if reconnect_attempt > 0:
                logger.info(f"Waiting {backoff}s before reconnection attempt {reconnect_attempt}...")
                await asyncio.sleep(backoff)

            logger.info("Connecting to WebSocket server...")
            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20,
                max_size=None
            ) as websocket:
                logger.info("Successfully connected to WebSocket server")

                # Start stdio MCP server as subprocess (same file with --server)
                process = subprocess.Popen(
                    [sys.executable, "-u", __file__, "--server"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding='utf-8',
                    text=True,
                    env=os.environ.copy()
                )
                logger.info("Started MCP stdio subprocess")

                await asyncio.gather(
                    pipe_websocket_to_process(websocket, process),
                    pipe_process_to_websocket(process, websocket),
                    pipe_process_stderr_to_terminal(process)
                )
        except Exception as e:
            reconnect_attempt += 1
            logger.warning(f"Connection closed (attempt {reconnect_attempt}): {e}")
            backoff = min(backoff * 2, max_backoff)
        finally:
            if process:
                logger.info("Terminating server subprocess")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                process = None

async def pipe_websocket_to_process(websocket, process):
    """Read from WebSocket and write to process stdin."""
    try:
        async for message in websocket:
            logger.debug(f"<< WS to process: {str(message)[:120]}...")
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            process.stdin.write(message + '\n')
            process.stdin.flush()
    except Exception as e:
        logger.error(f"WS→proc pipe error: {e}")
        raise

async def pipe_process_to_websocket(process, websocket):
    """Read from process stdout and send to WebSocket."""
    try:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, process.stdout.readline)
            if not line:
                logger.info("Process stdout closed")
                break
            logger.debug(f">> proc to WS: {line[:120]}...")
            await websocket.send(line.strip())
    except Exception as e:
        logger.error(f"proc→WS pipe error: {e}")
        raise

async def pipe_process_stderr_to_terminal(process):
    """Read from process stderr and print to terminal."""
    try:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, process.stderr.readline)
            if not line:
                logger.info("Process stderr closed")
                break
            sys.stderr.write(line)
            sys.stderr.flush()
    except Exception as e:
        logger.error(f"proc stderr pipe error: {e}")
        raise

def _graceful_exit(*_):
    logger.info("Received shutdown signal, exiting...")
    sys.exit(0)

if __name__ == "__main__":
    # Handle Railway/Render SIGTERM too
    signal.signal(signal.SIGINT, _graceful_exit)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _graceful_exit)

    if "--server" in sys.argv:
        logger.info("Running as MCP server with stdio transport")
        os.environ["PYTHONUNBUFFERED"] = "1"
        mcp.run(transport="stdio")
    else:
        endpoint_url = os.getenv('MCP_ENDPOINT')
        if not endpoint_url:
            logger.error("Please set the `MCP_ENDPOINT` environment variable")
            sys.exit(1)

        try:
            asyncio.run(connect_with_retry(endpoint_url))
        except SystemExit:
            pass
        except Exception as e:
            logger.error(f"Program execution error: {e}")
            sys.exit(1)
