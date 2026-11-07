#!/bin/bash
# AdaptMach launcher. Run with:  bash start.sh
cd "$(dirname "$0")"

if [ -z "$MODEL_API_KEY" ]; then
  echo ""
  echo "  Which AI model should power AdaptMach?"
  echo "    1) Meta Model API (muse-spark-1.3)   <- eligible for the Meta prize"
  echo "    2) Google Gemini"
  echo "    3) Subquadratic"
  echo "    4) No key: offline MOCK test mode (not for the demo)"
  read -p "  Choose 1-4: " CHOICE
  if [ "$CHOICE" != "4" ]; then
    read -s -p "  Paste your API key (hidden): " KEY; echo ""
    export MODEL_API_KEY="$KEY"
  fi
  case "$CHOICE" in
    2) export MODEL_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai"
       export MODEL_NAME="${MODEL_NAME:-gemini-2.5-flash}" ;;
    3) export MODEL_BASE_URL="${MODEL_BASE_URL:-https://api.subq.ai/v1}"
       export MODEL_NAME="${MODEL_NAME:-subq-preview}" ;;
    4) export ADAPTMACH_MOCK=1 ;;
  esac
  if [ "$CHOICE" = "1" ] && [ -z "$REFLECT_API_KEY" ]; then
    echo ""
    echo "  Optional: use Subquadratic as the LEARNING model (writes the lessons)."
    read -s -p "  Paste Subquadratic key, or just press Enter to skip: " SUBQ; echo ""
    if [ -n "$SUBQ" ]; then
      export REFLECT_API_KEY="$SUBQ"
      export REFLECT_BASE_URL="${REFLECT_BASE_URL:-https://api.subq.ai/v1}"
      export REFLECT_MODEL="${REFLECT_MODEL:-subq-preview}"
    fi
  fi
fi

(sleep 2; open "http://localhost:${PORT:-8000}" 2>/dev/null) &
python3 server.py
