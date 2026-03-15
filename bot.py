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

BASESCAN_TX_URL: str = "https://sepolia.basescan.org/tx/"
OPG_APPROVAL_AMOUNT: float = 5.0
DEFAULT_MODEL: og.TEE_LLM = og.TEE_LLM.GEMINI_2_5_FLASH
MAX_TOKENS: int = 800

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")
if not OG_PRIVATE_KEY:
    raise ValueError("OG_PRIVATE_KEY not set in .env")

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
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPStatusError as e:
        logger.warning(f"GitHub fetch {url} → HTTP {e.response.status_code}")
        return None
    except httpx.RequestError as e:
        logger.warning(f"GitHub fetch network error for {url}: {e}")
        return None


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
async def ask_llm(question: str, context: Optional[str] = None) -> tuple[str, str]:
    """
    Send a question to og.LLM, optionally with a code snippet as context.

    Args:
        question: User's question
        context: Optional code to inject as knowledge context

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

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        result = await llm.chat(
            model=DEFAULT_MODEL,
            messages=messages,
            max_tokens=MAX_TOKENS,
            temperature=0.1,
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
    except Exception as e:
        logger.error(f"LLM chat call failed: {e}")
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
        f"⏳ Fetching *{description}* and generating explanation...",
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
        logger.error(f"LLM error for {path}: {e}")
        await thinking_msg.edit_text(
            f"❌ LLM error: `{html.escape(str(e))}`\n\n"
            f"[Open file on GitHub]({snippet_github_url(path)})",
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
            f"{answer}\n\n"
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
    1. Try to detect a relevant snippet based on keywords
    2. Fetch it from GitHub if found
    3. Call og.LLM with (or without) code context
    4. Reply with answer + TEE proof footer
    """
    user_text = message.text.strip()
    snippet_path = detect_intent(user_text)

    thinking_msg = await message.answer("⏳ Thinking...")

    code_context: Optional[str] = None
    if snippet_path:
        code_context = await fetch_snippet(snippet_path)
        if code_context:
            logger.info(f"Injecting context from: {snippet_path}")

    try:
        answer, payment_hash = await ask_llm(user_text, code_context)
    except Exception as e:
        logger.error(f"LLM error: {e}")
        await thinking_msg.edit_text(
            f"❌ LLM error: `{html.escape(str(e))}`\n\n"
            "Check your OPG balance: /faucet",
            parse_mode="Markdown",
        )
        return

    safe_answer = escape_markdown(answer)
    proof = format_proof_line(payment_hash)
    context_note = ""
    if snippet_path:
        context_note = f"\n\n📎 _Context used:_ [{snippet_path}]({snippet_github_url(snippet_path)})"

    await thinking_msg.edit_text(
        f"{safe_answer}{context_note}\n\n{proof}",
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

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
