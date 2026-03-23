"""
og-helper-bot/bot.py

OpenGradient Helper — open-source Telegram bot for OpenGradient ecosystem developers.
Every AI response is generated via a TEE-verified og.LLM call and includes
a payment_hash as cryptographic proof of execution.

Dependencies: aiogram==3.13.1, opengradient>=0.1.0, httpx>=0.27.0, python-dotenv>=1.0.0
"""

import asyncio
import logging
import os
import re
import html
from typing import Optional

import httpx
import opengradient as og
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("og-helper-bot")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
OG_PRIVATE_KEY: str = os.getenv("OG_PRIVATE_KEY", "")
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "your_username/OpenGradient-Cookbook")
GITHUB_RAW_BASE: str = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"
DOCS_BASE_URL: str = "https://docs.opengradient.ai"

BASESCAN_TX_URL: str = "https://sepolia.basescan.org/tx/"
OPG_APPROVAL_AMOUNT: float = 5.0
DEFAULT_MODEL: og.TEE_LLM = og.TEE_LLM.GEMINI_2_5_FLASH
MAX_TOKENS: int = 800

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")
if not OG_PRIVATE_KEY:
    raise ValueError("OG_PRIVATE_KEY not set in .env")

# ---------------------------------------------------------------------------
# Per-user conversation history (in-memory, resets on bot restart)
# ---------------------------------------------------------------------------
# Stores the last MAX_HISTORY_TURNS exchanges per user.
# Key: Telegram user_id (int)
# Value: list of message dicts with "role" and "content" keys
MAX_HISTORY_TURNS: int = 3  # keep last 3 user+assistant pairs = 6 messages
conversation_history: dict[int, list[dict]] = {}


def get_history(user_id: int) -> list[dict]:
    """Return the conversation history for a user, or empty list if none."""
    return conversation_history.get(user_id, [])


def add_to_history(user_id: int, user_msg: str, assistant_msg: str) -> None:
    """
    Append a user+assistant exchange to the history.
    Trims to MAX_HISTORY_TURNS pairs automatically.
    """
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    history = conversation_history[user_id]
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})

    # Keep only the last MAX_HISTORY_TURNS pairs (each pair = 2 messages)
    max_messages = MAX_HISTORY_TURNS * 2
    if len(history) > max_messages:
        conversation_history[user_id] = history[-max_messages:]

# ---------------------------------------------------------------------------
# Intent map: keywords → cookbook file path
# ---------------------------------------------------------------------------
SNIPPET_INTENT_MAP: list[tuple[list[str], str]] = [
    (["completion", "llm", "inference", "basic call", "first request"], "snippets/01_llm_completion_basic.py"),
    (["tool", "function calling", "tool call", "tools"], "snippets/02_llm_chat_with_tools.py"),
    (["stream", "streaming", "real-time", "typewriter"], "snippets/03_llm_streaming.py"),
    (["settlement", "private mode", "individual full", "batch hashed"], "snippets/04_settlement_modes.py"),
    (["model hub", "upload", "onnx", "model management", "model repo"], "snippets/05_model_hub_management.py"),
    (["balance", "opg balance", "wallet", "check opg", "funds"], "snippets/06_check_opg_balance.py"),
    (["memsync", "memory", "personalized", "persistent memory"], "snippets/07_memsync_personalized_bot.py"),
    (["zkml", "zk proof", "ml inference", "on-chain ml", "zkml"], "snippets/08_ml_inference_alpha.py"),
    (["workflow", "schedule", "oracle", "volatility", "automated"], "snippets/09_ml_workflow_deploy.py"),
    (["permit2", "approve", "allowance", "opg approval"], "snippets/10_permit2_approval.py"),
    (["langchain", "react agent", "langchain agent", "lc"], "snippets/11_langchain_agent.py"),
    (["fastapi", "backend", "rest api", "server", "api server"], "boilerplates/fastapi-verifiable-backend/main.py"),
    (["defi", "audit", "smart contract", "reentrancy", "vulnerability"], "boilerplates/defi-risk-analyzer/analyzer.py"),
    (["digital twin", "twin.fun", "bonding curve", "shares"], "boilerplates/digital-twins-tracker/tracker.py"),
]

# ---------------------------------------------------------------------------
# Docs intent map: keywords → docs page path (relative to DOCS_BASE_URL)
# ---------------------------------------------------------------------------
# Maps user question keywords to the most relevant documentation page.
# These are fetched live from docs.opengradient.ai and injected as context.
DOCS_INTENT_MAP: list[tuple[list[str], str]] = [
    (
        ["tee", "trusted execution", "attestation", "hardware proof", "intel tdx", "enclave"],
        "learn/onchain_inference/llm_execution.html",
    ),
    (
        ["settlement", "settlement mode", "batch hashed", "individual full", "private mode", "on-chain proof"],
        "developers/sdk/llm.html",
    ),
    (
        ["x402", "payment protocol", "402 payment", "payment required", "x402 gateway"],
        "developers/x402/",
    ),
    (
        ["architecture", "how does opengradient work", "inference node", "full node", "network design"],
        "learn/architecture/",
    ),
    (
        ["memsync", "persistent memory", "memory api", "memory layer", "user profile", "semantic memory"],
        "developers/memsync/tutorial.html",
    ),
    (
        ["model hub", "walrus", "onnx format", "model restrictions", "model upload", "model registry"],
        "models/model_hub/",
    ),
    (
        ["use case", "use cases", "what can i build", "what to build", "applications"],
        "about/use_cases.html",
    ),
    (
        ["deploy", "deployment", "rpc url", "chain id", "testnet", "network config"],
        "learn/network/deployment.html",
    ),
]


def detect_docs_intent(text: str) -> Optional[str]:
    """
    Match a user message to the most relevant documentation page path.

    Returns:
        Relative docs path (e.g. 'learn/onchain_inference/llm_execution.html')
        if matched, None otherwise.
    """
    text_lower = text.lower()
    for keywords, path in DOCS_INTENT_MAP:
        if any(kw in text_lower for kw in keywords):
            return path
    return None

# ---------------------------------------------------------------------------
# Static text
# ---------------------------------------------------------------------------
START_TEXT = """
👋 Hey! I'm **OpenGradient Helper** — an open-source bot built by the community.

I help Web3 and AI developers build on **OpenGradient**, the infrastructure for Verifiable AI.

🔐 *My answers are generated inside a TEE enclave. Every response includes a* `payment_hash` *as on-chain proof.*

**What I can do:**
• Explain code from the [OpenGradient Cookbook](https://github.com/{GITHUB_REPO})
• Answer questions about x402 / TEE / MemSync / Model Hub
• Show code snippets with inline buttons

**Commands:**
/about — What is OpenGradient?
/snippets — Browse the Cookbook snippet catalog
/models — Models available in the Model Hub
/faucet — Get testnet $OPG tokens
""".strip()

ABOUT_TEXT = """
**🧠 What is OpenGradient?**

OpenGradient is decentralized infrastructure for **Verifiable AI** — where every AI inference produces cryptographic proof.

**Core components:**

🔐 **x402 Gateway** — payment-gated LLM inference via $OPG
Each request runs inside a TEE (Intel TDX) and records a `payment_hash` on-chain.

🗄 **Model Hub** — permissionless ONNX model registry on Walrus
[hub.opengradient.ai](https://hub.opengradient.ai) — 2000+ models ready for verified inference.

🧠 **MemSync** — persistent memory layer for AI agents
Automatically extracts facts from conversations and retrieves them across sessions.

👥 **Digital Twins (Twin.fun)** — AI personas tokenized on a bonding curve

**Network:** Base Sepolia Testnet
**Token:** $OPG (`0x240b09731D96979f50B2C649C9CE10FcF9C7987F`)

[📚 Docs](https://docs.opengradient.ai) | [🔍 Explorer](https://explorer.opengradient.ai) | [💬 Discord](https://discord.gg/SC45QNNMsB)
""".strip()

FAUCET_TEXT = """
🚰 **Get testnet $OPG tokens**

$OPG is needed to pay for LLM inference on Base Sepolia.

👉 [faucet.opengradient.ai](https://faucet.opengradient.ai)

After getting tokens, verify your balance via /snippets → *Check OPG Balance*.
""".strip()

SYSTEM_PROMPT = """
You are OpenGradient Helper, an open-source AI assistant for developers building on OpenGradient.
Your goal is to help users write correct, working code for the OpenGradient ecosystem.

Key facts about OpenGradient:
1. LLM inference uses og.LLM (async API) paid with $OPG on Base Sepolia (Chain ID: 84532)
2. Every request runs inside a TEE (Intel TDX) and records a payment_hash on-chain
3. Initialization: llm = og.LLM(private_key=os.getenv("OG_PRIVATE_KEY"))
4. Before first request: llm.ensure_opg_approval(opg_amount=5.0)
5. Chat call: result = await llm.chat(model=og.TEE_LLM.GPT_5, messages=[...])
6. Result fields: result.chat_output["content"] and result.payment_hash
7. ML inference (alpha testnet only): alpha = og.Alpha(private_key=...) → alpha.infer(...)
8. Model Hub: hub = og.ModelHub(email=..., password=...) → hub.upload(...)
9. MemSync REST API base: https://api.memchat.io/v1 — auth via X-API-Key header
10. Supported models: og.TEE_LLM.GPT_5, CLAUDE_SONNET_4_6, GEMINI_2_5_FLASH, GROK_4

Response rules:
- Be concise and technically precise (3-4 paragraphs max)
- If code is provided as context, explain it — don't just paraphrase it line by line
- Always wrap code in ```python ... ``` blocks
- Honestly state you are an unofficial open-source community bot, not affiliated with OpenGradient
- If a question is off-topic, politely redirect to OpenGradient topics
- Use English only
""".strip()

# ---------------------------------------------------------------------------
# OpenGradient LLM singleton
# ---------------------------------------------------------------------------
_llm: Optional[og.LLM] = None


async def get_llm() -> og.LLM:
    """
    Initialize og.LLM singleton and ensure Permit2 OPG approval.
    Called once at startup and cached for the process lifetime.
    """
    global _llm
    if _llm is None:
        logger.info("Initializing og.LLM...")
        try:
            # Assume og.LLM init is light, but the approval check is a network call
            _llm = og.LLM(private_key=OG_PRIVATE_KEY)
            # CRITICAL: ensure_opg_approval is an on-chain/network call but not async in this SDK
            approval = _llm.ensure_opg_approval(opg_amount=OPG_APPROVAL_AMOUNT)
            if hasattr(approval, "tx_hash") and approval.tx_hash:
                logger.info(f"💰 Permit2 approval tx: {BASESCAN_TX_URL}{approval.tx_hash}")
            else:
                logger.info("💰 Permit2 allowance already sufficient")
        except Exception as e:
            logger.error(f"og.LLM initialization or Permit2 approval failed: {e}")
            _llm = None  # Reset so it can be retried if it's transient
            raise RuntimeError(
                f"OPG initialization failed: {e}\n"
                f"Check your balance at: https://faucet.opengradient.ai"
            ) from e
    return _llm


async def reset_llm():
    """Clear the LLM singleton to force re-initialization."""
    global _llm
    if _llm:
        try:
            await _llm.close()
        except:
            pass
        _llm = None
    logger.info("♻️ og.LLM singleton reset")


# ---------------------------------------------------------------------------
# GitHub snippet fetcher
# ---------------------------------------------------------------------------
async def fetch_snippet(path: str) -> Optional[str]:
    """
    Fetch a raw file from the Cookbook GitHub repo.

    Args:
        path: Relative file path (e.g. 'snippets/01_llm_completion_basic.py')

    Returns:
        File contents as string, or None on network/404 error
    """
    url = f"{GITHUB_RAW_BASE}/{path}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPStatusError as e:
        logger.warning(f"GitHub fetch {url} → HTTP {e.response.status_code}")
        return None
    except httpx.RequestError as e:
        logger.warning(f"GitHub fetch network error for {url}: {e}")
        return None


async def fetch_docs(path: str) -> Optional[str]:
    """
    Fetch a documentation page from docs.opengradient.ai and return clean text.

    Fetches the HTML page, strips all HTML tags, collapses whitespace,
    and truncates to 4000 characters to stay within LLM token budget.

    Args:
        path: Relative path to the docs page, e.g. 'learn/onchain_inference/llm_execution.html'

    Returns:
        Clean text content of the page, or None on error.
    """
    url = f"{DOCS_BASE_URL}/{path}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw_html = resp.text
    except httpx.HTTPStatusError as e:
        logger.warning(f"Docs fetch {url} → HTTP {e.response.status_code}")
        return None
    except httpx.RequestError as e:
        logger.warning(f"Docs fetch network error for {url}: {e}")
        return None

    # Strip HTML tags using a simple regex — no extra dependencies needed
    # Remove script and style blocks first (they contain no useful text)
    no_scripts = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining HTML tags
    no_tags = re.sub(r"<[^>]+>", " ", no_scripts)
    # Collapse multiple whitespace/newlines into single spaces
    clean = re.sub(r"\s+", " ", no_tags).strip()

    # Truncate to 4000 chars — enough for good context without overloading the LLM
    if len(clean) > 4000:
        clean = clean[:4000] + " ... [content truncated — see full docs at the link below]"

    logger.info(f"Fetched docs page: {url} ({len(clean)} chars)")
    return clean


def detect_intent(text: str) -> Optional[str]:
    """
    Match a user message to the most relevant Cookbook snippet path.

    Returns:
        Relative GitHub path if matched, None otherwise
    """
    text_lower = text.lower()
    for keywords, path in SNIPPET_INTENT_MAP:
        if any(kw in text_lower for kw in keywords):
            return path
    return None


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
async def ask_llm(
    question: str,
    context: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> tuple[str, str]:
    """
    Send a question to og.LLM with optional code context and conversation history.

    Args:
        question: User's current question
        context: Optional code snippet or docs page to inject as knowledge context
        history: Optional list of previous {"role", "content"} message dicts

    Returns:
        Tuple of (answer_text, payment_hash)
    """
    llm = await get_llm()

    if context:
        # Limit context to ~3000 chars to stay within token budget
        truncated = context[:3000] + ("\n... [truncated — see GitHub for full file]" if len(context) > 3000 else "")
        user_content = (
            f"Here is the relevant code from the OpenGradient Cookbook:\n\n"
            f"```python\n{truncated}\n```\n\n"
            f"User question: {question}"
        )
    else:
        user_content = question

    # Build messages: system prompt + history + current user message
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": user_content})

    try:
        result = await asyncio.wait_for(
            llm.chat(
                model=DEFAULT_MODEL,
                messages=messages,
                max_tokens=MAX_TOKENS,
                temperature=0.1,
            ),
            timeout=45.0,
        )

        if not result or not hasattr(result, "chat_output"):
            logger.error("LLM returned an empty or invalid result object")
            return "❌ Error: LLM returned no data.", ""

        content = result.chat_output.get("content", "") if result.chat_output else ""

        # Try different hash fields depending on SDK version/gateway setup
        payment_hash = getattr(result, "payment_hash", None)
        if not payment_hash:
            payment_hash = getattr(result, "transaction_hash", "")

        return content, (payment_hash or "")

    except asyncio.TimeoutError:
        logger.warning("LLM call timed out after 45s")
        raise RuntimeError("LLM request timed out — TEE node is slow. Please try again.")

    except Exception as e:
        logger.error(f"LLM chat call failed: {e}")
        # If it's a payment or protocol error, reset the singleton to try a different TEE next time
        if "payment" in str(e).lower() or "TEE" in str(e):
            await reset_llm()
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def snippet_github_url(path: str) -> str:
    """Return the GitHub HTML URL for a cookbook file."""
    return f"https://github.com/{GITHUB_REPO}/blob/main/{path}"


def format_proof_line(payment_hash: str) -> str:
    """Format the TEE proof footer shown under every LLM response."""
    if not payment_hash:
        return "🔐 _Generated in TEE_ | No payment hash recorded"
    short = payment_hash[:20] + "..."
    url = f"{BASESCAN_TX_URL}{payment_hash}"
    return f"🔐 _Generated in TEE_ | [Proof: `{short}`]({url})"


def truncate_code_for_telegram(code: str, max_chars: int = 1200) -> str:
    """
    Truncate a code string to fit within Telegram's message character limit.
    Cuts at a line boundary and appends a note pointing to GitHub.
    """
    if len(code) <= max_chars:
        return code
    lines = code.split("\n")
    result = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > max_chars:
            result.append("# ... file truncated — see GitHub for the full version")
            break
        result.append(line)
        total += len(line) + 1
    return "\n".join(result)


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram Markdown (V1).
    Characters to escape: _, *, `, [
    """
    # Specifically for Markdown V1, we mostly care about unclosed formatting symbols.
    # However, for the 'answer' part, we want to allow the LLM to use some formatting.
    # A safer approach for user-generated content is escaping.
    # For now, we'll do a simple escape of the most problematic ones if they aren't part of a tag.
    # But since we want to ALLOW certain markdown, we'll be surgical.
    if not text:
        return ""
    # We'll at least escape lone underscores which are common in variable names and crash Markdown V1
    return text.replace("_", "\\_")


# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------
def snippets_keyboard() -> InlineKeyboardMarkup:
    """Inline menu for /snippets — all 11 snippets + 3 boilerplates."""
    buttons = [
        [
            InlineKeyboardButton(text="🤖 LLM Completion", callback_data="snip:01"),
            InlineKeyboardButton(text="🔧 Tool Calling", callback_data="snip:02"),
        ],
        [
            InlineKeyboardButton(text="📡 Streaming", callback_data="snip:03"),
            InlineKeyboardButton(text="⚖️ Settlement Modes", callback_data="snip:04"),
        ],
        [
            InlineKeyboardButton(text="🗄 Model Hub", callback_data="snip:05"),
            InlineKeyboardButton(text="💰 Check Balance", callback_data="snip:06"),
        ],
        [
            InlineKeyboardButton(text="🧠 MemSync", callback_data="snip:07"),
            InlineKeyboardButton(text="⚗️ ML Inference", callback_data="snip:08"),
        ],
        [
            InlineKeyboardButton(text="⏰ ML Workflow", callback_data="snip:09"),
            InlineKeyboardButton(text="🔐 Permit2", callback_data="snip:10"),
        ],
        [
            InlineKeyboardButton(text="🦜 LangChain Agent", callback_data="snip:11"),
        ],
        [
            InlineKeyboardButton(text="🚀 FastAPI Backend", callback_data="bp:fastapi"),
            InlineKeyboardButton(text="🏦 DeFi Analyzer", callback_data="bp:defi"),
        ],
        [
            InlineKeyboardButton(text="👥 Digital Twins", callback_data="bp:twins"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def models_keyboard() -> InlineKeyboardMarkup:
    """Inline menu for /models — links to official OG Model Hub entries."""
    buttons = [
        [InlineKeyboardButton(
            text="📈 1hr ETH/USD Volatility",
            url="https://hub.opengradient.ai/models/OpenGradient/og-1hr-volatility-ethusdt",
        )],
        [InlineKeyboardButton(
            text="🪙 30min SUI/USD Return",
            url="https://hub.opengradient.ai/models/OpenGradient/og-30min-return-suiusdt",
        )],
        [InlineKeyboardButton(
            text="📉 6hr SUI/USD Return",
            url="https://hub.opengradient.ai/models/OpenGradient/og-6h-return-suiusdt",
        )],
        [InlineKeyboardButton(
            text="📚 Browse All Models",
            url="https://hub.opengradient.ai",
        )],
        [InlineKeyboardButton(
            text="📖 Model Hub Docs",
            url="https://docs.opengradient.ai/models/model_hub/",
        )],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Callback ID → cookbook file path (must match snippets_keyboard buttons)
CALLBACK_TO_PATH: dict[str, str] = {
    "snip:01": "snippets/01_llm_completion_basic.py",
    "snip:02": "snippets/02_llm_chat_with_tools.py",
    "snip:03": "snippets/03_llm_streaming.py",
    "snip:04": "snippets/04_settlement_modes.py",
    "snip:05": "snippets/05_model_hub_management.py",
    "snip:06": "snippets/06_check_opg_balance.py",
    "snip:07": "snippets/07_memsync_personalized_bot.py",
    "snip:08": "snippets/08_ml_inference_alpha.py",
    "snip:09": "snippets/09_ml_workflow_deploy.py",
    "snip:10": "snippets/10_permit2_approval.py",
    "snip:11": "snippets/11_langchain_agent.py",
    "bp:fastapi": "boilerplates/fastapi-verifiable-backend/main.py",
    "bp:defi":    "boilerplates/defi-risk-analyzer/analyzer.py",
    "bp:twins":   "boilerplates/digital-twins-tracker/tracker.py",
}

CALLBACK_DESCRIPTIONS: dict[str, str] = {
    "snip:01": "LLM Completion Basic",
    "snip:02": "Chat with Tool Calling",
    "snip:03": "Streaming Chat",
    "snip:04": "Settlement Modes",
    "snip:05": "Model Hub Management",
    "snip:06": "Check OPG Balance",
    "snip:07": "MemSync Personalized Bot",
    "snip:08": "ML Inference Alpha",
    "snip:09": "ML Workflow Deploy",
    "snip:10": "Permit2 Approval",
    "snip:11": "LangChain Agent",
    "bp:fastapi": "FastAPI Verifiable Backend",
    "bp:defi":    "DeFi Risk Analyzer",
    "bp:twins":   "Digital Twins Tracker",
}

# ---------------------------------------------------------------------------
# Bot + Dispatcher
# ---------------------------------------------------------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------------------------------------------------------------------------
# Static command handlers (no LLM needed)
# ---------------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = START_TEXT.replace("{GITHUB_REPO}", GITHUB_REPO)
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)


@dp.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.answer(ABOUT_TEXT, parse_mode="Markdown", disable_web_page_preview=True)


@dp.message(Command("faucet"))
async def cmd_faucet(message: Message) -> None:
    await message.answer(FAUCET_TEXT, parse_mode="Markdown", disable_web_page_preview=True)


@dp.message(Command("snippets"))
async def cmd_snippets(message: Message) -> None:
    await message.answer(
        "📚 *OpenGradient Cookbook — Snippet Catalog*\n\n"
        "Pick a topic — I'll fetch the code and explain it:",
        parse_mode="Markdown",
        reply_markup=snippets_keyboard(),
    )


@dp.message(Command("models"))
async def cmd_models(message: Message) -> None:
    await message.answer(
        "🗄 *OpenGradient Model Hub*\n\n"
        "Permissionless ONNX model registry built on Walrus.\n"
        "Official OpenGradient models:",
        parse_mode="Markdown",
        reply_markup=models_keyboard(),
    )


# ---------------------------------------------------------------------------
# Callback: snippet button tapped
# ---------------------------------------------------------------------------
@dp.callback_query(F.data.startswith("snip:") | F.data.startswith("bp:"))
async def callback_snippet(query: CallbackQuery) -> None:
    """Fetch the selected cookbook file, generate an LLM explanation, and reply."""
    await query.answer()  # dismiss the loading spinner on the button

    path = CALLBACK_TO_PATH.get(query.data)
    description = CALLBACK_DESCRIPTIONS.get(query.data, query.data)

    if not path:
        await query.message.answer("❌ Unknown button.")
        return

    thinking_msg = await query.message.answer(
        f"⏳ Fetching *{description}* and generating explanation...\n"
        f"_This may take up to 45 seconds..._",
        parse_mode="Markdown",
    )

    # Fetch code from GitHub
    code = await fetch_snippet(path)
    if not code:
        await thinking_msg.edit_text(
            f"❌ Could not load file from GitHub.\n"
            f"[Open directly]({snippet_github_url(path)})",
            parse_mode="Markdown",
        )
        return

    # Generate explanation via og.LLM (TEE-verified)
    try:
        answer, payment_hash = await ask_llm(
            question="Explain this file: what it does, how to use it, and what to watch out for.",
            context=code,
        )
    except Exception as e:
        logger.warning(f"First LLM attempt for callback failed, retrying: {e}")
        try:
            answer, payment_hash = await ask_llm(
                question="Explain this file: what it does, how to use it, and what to watch out for.",
                context=code,
            )
        except Exception as e2:
            logger.error(f"LLM error for {path} after retry: {e2}")
            await thinking_msg.edit_text(
                f"❌ LLM error: `{html.escape(str(e2))}`\n\n"
                f"[Open file on GitHub]({snippet_github_url(path)})\n"
                f"The OpenGradient TEE gateway might be experiencing high load.",
                parse_mode="Markdown",
            )
            return

    # Escape answer text to prevent Markdown crashes from unclosed _ or *
    safe_answer = escape_markdown(answer)
    code_preview = truncate_code_for_telegram(code, max_chars=1200)
    github_url = snippet_github_url(path)
    proof = format_proof_line(payment_hash)

    # Combined length check. Max is 4096. 
    # Answer might have special chars, proof too.
    # We use a safer buffer of 4000 to account for escaping if switched later.
    full_text = (
        f"📄 *{description}*\n\n"
        f"{safe_answer}\n\n"
        f"{proof}"
    )
    
    # If the text part alone is very long, we might need to truncate the answer or split.
    # Here we split code into its own message if collective length is too big.
    if len(full_text) + len(code_preview) + 20 > 4000:
        await thinking_msg.edit_text(
            f"📄 *{description}*\n\n{safe_answer}\n\n{proof}",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        # Send code separately
        await query.message.answer(
            f"```python\n{code_preview}\n```\n\n[📂 Full code on GitHub]({github_url})",
            parse_mode="Markdown",
        )
    else:
        response = (
            f"📄 *{description}*\n\n"
            f"{safe_answer}\n\n"
            f"```python\n{code_preview}\n```\n\n"
            f"[📂 Full file on GitHub]({github_url})\n\n"
            f"{proof}"
        )
        await thinking_msg.edit_text(
            response,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )


# ---------------------------------------------------------------------------
# Free-form text handler
# ---------------------------------------------------------------------------
@dp.message(F.text)
async def handle_text(message: Message) -> None:
    """
    Handle any plain text message:
    1. Load this user's conversation history
    2. Try to detect a relevant snippet based on keywords
    3. Fetch it from GitHub if found
    4. Call og.LLM with history + (optional) code context
    5. Save the exchange to history
    6. Reply with answer + TEE proof footer
    """
    user_text = message.text.strip()
    user_id = message.from_user.id
    snippet_path = detect_intent(user_text)

    thinking_msg = await message.answer("⏳ Thinking...")

    code_context: Optional[str] = None
    context_source_note: str = ""  # shown to user at bottom of reply

    if snippet_path:
        # Snippet takes priority over docs
        code_context = await fetch_snippet(snippet_path)
        if code_context:
            logger.info(f"Injecting context from snippet: {snippet_path}")
            context_source_note = f"📎 _Context used:_ [{snippet_path}]({snippet_github_url(snippet_path)})"
    else:
        # No snippet matched — try docs pages
        docs_path = detect_docs_intent(user_text)
        if docs_path:
            docs_text = await fetch_docs(docs_path)
            if docs_text:
                code_context = docs_text
                docs_url = f"{DOCS_BASE_URL}/{docs_path}"
                logger.info(f"Injecting context from docs: {docs_url}")
                context_source_note = f"📖 _Source:_ [docs.opengradient.ai/{docs_path}]({docs_url})"

    # Load this user's history for multi-turn context
    history = get_history(user_id)

    try:
        answer, payment_hash = await ask_llm(user_text, code_context, history)
    except Exception as e:
        logger.warning(f"First LLM attempt failed, retrying with fresh client: {e}")
        try:
            answer, payment_hash = await ask_llm(user_text, code_context, history)
        except Exception as e2:
            logger.error(f"LLM error after retry: {e2}")
            await thinking_msg.edit_text(
                f"❌ LLM error: `{html.escape(str(e2))}`\n\n"
                "The OpenGradient TEE gateway might be experiencing high load. "
                "Check your OPG balance: /faucet",
                parse_mode="Markdown",
            )
            return

    # Save this exchange to history BEFORE formatting
    add_to_history(user_id, user_text, answer)

    safe_answer = escape_markdown(answer)
    proof = format_proof_line(payment_hash)

    # context_source_note was set above (either snippet or docs, or empty string)
    footer = f"\n\n{context_source_note}" if context_source_note else ""

    await thinking_msg.edit_text(
        f"{safe_answer}{footer}\n\n{proof}",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
async def main() -> None:
    logger.info("Starting OpenGradient Helper Bot...")

    # Pre-warm og.LLM and run Permit2 approval at boot — not on first user request
    try:
        await get_llm()
        logger.info("✅ og.LLM ready — Permit2 approved")
    except Exception as e:
        logger.error(f"LLM init failed: {e}")
        logger.warning("Bot will start, but LLM calls will fail until OPG is funded")

    # Register native Bot Commands (Menu)
    commands = [
        BotCommand(command="start", description="Welcome message"),
        BotCommand(command="about", description="What is OpenGradient?"),
        BotCommand(command="snippets", description="Browse Cookbook snippets"),
        BotCommand(command="models", description="Browse Model Hub"),
        BotCommand(command="faucet", description="Get testnet OPG tokens"),
    ]
    await bot.set_my_commands(commands)
    logger.info("✅ Bot commands (menu) registered")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
