import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from groq import Groq
from mcp import Client, StdioServerParameters

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)

# Use a currently supported tool-calling model by default.
configured_model = os.getenv("GROQ_MODEL", "").strip()
deprecated_models = {
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "llama3-8b-8192",
    "llama3-70b-8192",
}

if (
    not configured_model
    or "your_supported_groq_model_id" in configured_model.lower()
    or configured_model in deprecated_models
):
    GROQ_MODEL = "openai/gpt-oss-20b"
else:
    GROQ_MODEL = configured_model

SYSTEM_PROMPT = """
You are MIRA, a friendly, playful cinema companion for Lahore, Pakistan.
Accuracy matters more than sounding confident.

CINEMA SEARCH:
- For every question about movie availability or showtimes, use the MCP
  search_showtimes tool first.
- Send only the core movie title in movie_title. Send the time preference
  separately; use "any" if there is no preference.
- Supabase/MCP results are the source of truth for local screening times,
  cinema names, prices and ticket links.
- Never invent a screening, time, cinema, price, or booking link.
- If results are returned, use them. Never claim no showtimes exist when
  the tool returned matching records.
- If match_status is "no_time_match", explain that the requested time did
  not match and offer the alternative screenings returned by the tool.
- If match_status is "search_error", explain that the search is temporarily
  unavailable. Do not report a database error as no screenings.
- Only report no upcoming showtimes when the tool returns
  "no_upcoming_showtimes" with zero results.
- Never say the scraper ran during a chat request.

RELEASE-DATE RESEARCH:
- When no upcoming showtimes are stored, use browser_search to check whether
  the exact film is unreleased, already released, or has an announced date.
- Prefer official studio, distributor or local cinema sources.
- Distinguish an international release date from a confirmed Pakistan or
  Lahore opening date.
- When a future installment has an announced date, answer in ONE OR TWO
  concise sentences. State the upcoming installment, its announced release
  window/date, and whether Lahore dates have been confirmed.
- Do not list earlier films in the franchise, past release dates, a release
  history table, or general movie recommendations unless the user asks.
- Do not turn an international release date into a claimed Lahore screening.
- If the date cannot be verified, say so instead of guessing.
- A single relevant official source link is enough; do not provide a
  long source roundup.

STYLE:
- Be warm, conversational and concise.
- Lead with the answer, not an apology.
- Format multiple actual showtimes as a readable Markdown table.
- Show ticket links as links, not as raw URLs.
- Never claim to book or purchase tickets.
- Answer ordinary conversational questions without tools when appropriate.
"""

MCP_SERVER_PATH = BASE_DIR / "mcp_server.py"


def get_groq_client() -> Groq:
    """Create a Groq client without exposing the API key."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key or api_key == "your_groq_api_key":
        raise RuntimeError(
            "GROQ_API_KEY is missing. Configure it in your private .env file."
        )

    return Groq(api_key=api_key)


def build_mcp_parameters() -> StdioServerParameters:
    """Launch the MCP server with only the environment it needs."""
    required = ("SUPABASE_URL", "SUPABASE_KEY")
    missing = [name for name in required if not os.getenv(name, "").strip()]

    if missing:
        raise RuntimeError(
            "Missing required environment settings: " + ", ".join(missing)
        )

    child_env = {
        name: os.environ[name]
        for name in (
            "SUPABASE_URL",
            "SUPABASE_KEY",
            "APP_ENV",
            "LOG_LEVEL",
            "TIMEZONE",
        )
        if os.getenv(name) is not None
    }

    return StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER_PATH)],
        env=child_env,
        cwd=str(BASE_DIR),
    )


def convert_mcp_tool(tool) -> dict:
    """Convert an MCP tool definition into Groq's function-tool format."""
    schema = getattr(tool, "inputSchema", None)

    if schema is None:
        schema = getattr(tool, "input_schema", None)

    if schema is None and hasattr(tool, "model_dump"):
        data = tool.model_dump(by_alias=True)
        schema = data.get("inputSchema") or data.get("input_schema")

    if hasattr(schema, "model_dump"):
        schema = schema.model_dump(by_alias=True)

    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


def normalize_history(history: list | None) -> list[dict]:
    """Accept only ordinary user and assistant messages."""
    cleaned = []

    if not isinstance(history, list):
        return cleaned

    for item in history[-10:]:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")

        if role in {"user", "assistant"} and isinstance(content, str):
            if content.strip():
                cleaned.append({"role": role, "content": content[:4000]})

    return cleaned


async def call_groq(client: Groq, messages: list, tools: list):
    """Run the synchronous Groq request without blocking the event loop."""
    def request():
        return client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )

    return await asyncio.to_thread(request)


def mcp_result_text(result) -> str:
    """Extract readable output from an MCP tool result."""
    parts = []

    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)

        if text is not None:
            parts.append(text)

    if parts:
        return "\n".join(parts)

    return json.dumps(
        {"error": "The MCP tool returned no readable content."}
    )


async def chat_with_cinema(
    user_message: str,
    history: list | None = None,
) -> str:
    """
    Answer a user through Groq and the Supabase-backed MCP tool.

    This function never invokes scraper.py or run_scrape_job().
    """
    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("Message cannot be empty.")

    if len(user_message) > 4000:
        raise ValueError("Message is too long. Keep it under 4,000 characters.")

    if not MCP_SERVER_PATH.is_file():
        raise RuntimeError(f"MCP server file not found: {MCP_SERVER_PATH}")

    groq_client = get_groq_client()

    today_lahore = datetime.now(ZoneInfo("Asia/Karachi"))
    dated_system_prompt = (
        SYSTEM_PROMPT
        + "\\nCurrent local date in Lahore: "
        + today_lahore.strftime("%A, %d %B %Y")
        + ". Use this date when interpreting release status and future dates."
    )
    messages = [{"role": "system", "content": dated_system_prompt}]
    messages.extend(normalize_history(history))
    messages.append({"role": "user", "content": user_message.strip()})

    # Each request gets its own MCP subprocess; the SDK cleans it up
    # when the context exits. Chat requests only query stored data.
    async with Client(build_mcp_parameters()) as mcp_client:
        tool_response = await mcp_client.list_tools()
        mcp_tools = tool_response.tools

        if not mcp_tools:
            raise RuntimeError("The MCP server has no registered tools.")

        groq_tools = [convert_mcp_tool(tool) for tool in mcp_tools]
        # Groq runs browser_search on its side; MCP tools remain our
        # explicit Supabase-backed cinema search.
        groq_tools.append({"type": "browser_search"})
        available_names = {tool.name for tool in mcp_tools}

        for _ in range(4):
            response = await call_groq(
                groq_client,
                messages,
                groq_tools,
            )

            assistant = response.choices[0].message
            tool_calls = assistant.tool_calls or []

            if not tool_calls:
                return (
                    assistant.content.strip()
                    if assistant.content
                    else "I couldn't generate a response. Please try again."
                )

            # Preserve the assistant's tool-call request for the next turn.
            messages.append({
                "role": "assistant",
                "content": assistant.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ],
            })

            for call in tool_calls:
                name = call.function.name

                if name not in available_names:
                    result_text = json.dumps({
                        "error": f"Unknown MCP tool: {name}"
                    })
                else:
                    try:
                        arguments = json.loads(call.function.arguments or "{}")

                        if not isinstance(arguments, dict):
                            raise ValueError("Tool arguments must be an object.")

                        result = await mcp_client.call_tool(name, arguments)
                        result_text = mcp_result_text(result)

                        if getattr(result, "is_error", False):
                            logger.warning("MCP tool %s returned an error.", name)

                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        result_text = json.dumps({
                            "error": f"Invalid tool call: {exc}"
                        })
                    except Exception:
                        logger.exception("MCP tool call failed: %s", name)
                        result_text = json.dumps({
                            "error": "The cinema database search failed."
                        })

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": result_text,
                })

        return (
            "I couldn't finish that search in the allowed number of steps. "
            "Please try a simpler movie query."
        )


if __name__ == "__main__":
    async def main():
        print("Lahore Cinema Chatbot")
        print(f"Groq model: {GROQ_MODEL}")
        print("Type 'exit' to quit.\n")

        while True:
            try:
                message = input("You: ").strip()

                if message.lower() in {"exit", "quit"}:
                    break

                if not message:
                    continue

                answer = await chat_with_cinema(message)
                print(f"\nAssistant: {answer}\n")

            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break
            except Exception as exc:
                logger.exception("Chat request failed.")
                print(f"\nChat error: {type(exc).__name__}: {exc}\n")

    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
