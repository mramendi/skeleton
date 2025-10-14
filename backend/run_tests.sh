#!/bin/bash
# Quick test runner script for Skeleton API

set -e  # Exit on error

echo "🧪 Running Skeleton API Test Suite"
echo "=================================="
echo ""

# Check if we're in the backend directory
if [ ! -f "main.py" ]; then
    echo "❌ Error: Please run this script from the backend directory"
    exit 1
fi

# Check if key dependencies are available (fastapi, pytest)
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "❌ Error: Skeleton dependencies not found"
    echo ""
    echo "Please either:"
    echo "  1. Enter the virtual environment for Skeleton:"
    echo "     source /path/to/venv/bin/activate"
    echo ""
    echo "  2. Install dependencies:"
    echo "     pip install -r requirements-test.txt"
    echo ""
    exit 1
fi

# Check if pytest is installed
if ! python3 -c "import pytest" 2>/dev/null; then
    echo "❌ Error: pytest not found"
    echo ""
    echo "Please either:"
    echo "  1. Enter the virtual environment for Skeleton:"
    echo "     source /path/to/venv/bin/activate"
    echo ""
    echo "  2. Install test dependencies:"
    echo "     pip install -r requirements-test.txt"
    echo ""
    exit 1
fi

# Run tests based on argument
case "${1:-all}" in
    "all")
        echo "Running all tests..."
        pytest -v
        ;;
    "fast")
        echo "Running unit tests only (fast)..."
        pytest -v -m unit
        ;;
    "coverage")
        echo "Running tests with coverage report..."
        pytest --cov=. --cov-report=term-missing --cov-report=html
        echo ""
        echo "✅ Coverage report generated in htmlcov/index.html"
        ;;
    "auth")
        echo "Running authentication tests..."
        pytest -v -m auth
        ;;
    "api")
        echo "Running API endpoint tests..."
        pytest -v -m api
        ;;
    "streaming")
        echo "Running streaming tests..."
        pytest -v -m streaming
        ;;
    "watch")
        echo "Running tests in watch mode..."
        if ! command -v pytest-watch &> /dev/null; then
            echo "❌ pytest-watch not installed. Install with: pip install pytest-watch"
            exit 1
        fi
        pytest-watch
        ;;
    *)
        echo "Usage: $0 [all|fast|coverage|auth|api|streaming|watch]"
        echo ""
        echo "  all       - Run all tests (default)"
        echo "  fast      - Run only unit tests"
        echo "  coverage  - Run tests with coverage report"
        echo "  auth      - Run authentication tests"
        echo "  api       - Run API endpoint tests"
        echo "  streaming - Run streaming tests"
        echo "  watch     - Run tests in watch mode (requires pytest-watch)"
        exit 1
        ;;
esac

echo ""
echo "✅ Tests completed!"
