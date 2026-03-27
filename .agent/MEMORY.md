# MEMORY

## Project Overview
- **Name**: OpenGradient Helper
- **Type**: AI Assistant Project Template
- **Base Template**: [antigravity-template](https://github.com/Floyd11/antigravity-template)

## Installed Skills
- **01-discovery-interview**: Deep interview process to transform vague ideas into detailed specs.
- **02-brainstorming**: Creative exploration of user intent and design.
- **03-ui-ux-pro-max**: UI/UX design intelligence and implementation.
- **04-fullstack-developer**: Full-stack web development expertise.
- **05-telegram-bot-builder**: Comprehensive guidance for building Telegram bots (installed 2026-03-15).
- **99-skill creator**: Guide for creating new skills.

## Recent Changes
- **2026-03-27**: Rewrote git history to attribute AI Agent commits to **Floyd11** (`floyd1611@gmail.com`). Set local git configuration to maintain this identity for future commits.
- **2026-03-26**: Upgraded `opengradient` SDK from 0.9.0 to 0.9.3. Verified TEE inference, Permit2 allowance, and successful bot startup.
- **2026-03-16**: Implemented retry mechanism for LLM calls in `bot.py` to handle potential gateway timeouts and refresh the client session.
- **2026-03-15**: Installed `Telegram Bot Builder` skill from `davila7/claude-code-templates`.
- **2026-03-15**: Conducted full QA & Security Audit of `og-helper-bot`. Implemented fixes for async/await, Markdown formatting, and message length handling.
- **2026-03-15**: Consolidated project structure by moving `.agent/`, `.gitignore`, and `GEMINI.md` from the scratch directory to the main project root.
