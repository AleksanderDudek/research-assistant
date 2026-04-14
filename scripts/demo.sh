#!/bin/bash
# Demo script: run the agent against a research question
set -e

QUESTION="${1:-Compare the 2026 pricing of Anthropic Claude vs OpenAI GPT-4 class models for a 1M-token-per-day workload, showing your math.}"
BUDGET="${2:-2.00}"

echo "Question: $QUESTION"
echo "Budget: \$$BUDGET"
echo ""

docker compose exec agent python -m agent.cli run "$QUESTION" --budget "$BUDGET"
