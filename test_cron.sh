#!/bin/bash
# Comprehensive test script for MiniVentory cron endpoints

# Configuration
SERVER="129.85.109.1"
PORT="2152"
TOKEN="20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6"
BASE_URL="http://${SERVER}:${PORT}"

echo "========================================"
echo "MiniVentory Cron Endpoint Test Script"
echo "========================================"
echo "Server: $SERVER:$PORT"
echo "Time: $(date)"
echo ""

# Test 1: Check if port is accessible
echo "[1/6] Testing port connectivity..."
if nc -zv $SERVER $PORT 2>&1 | grep -q "succeeded\|open"; then
    echo "✅ Port $PORT is accessible"
else
    echo "❌ Port $PORT is NOT accessible (Connection refused)"
    echo "   → Check: Is Docker container running? docker ps"
    echo "   → Check: Is port exposed? docker port <container_name>"
    exit 1
fi
echo ""

# Test 2: Health endpoint
echo "[2/6] Testing /health endpoint..."
HEALTH=$(curl -s -w "\nHTTP_CODE:%{http_code}" "${BASE_URL}/health" 2>&1)
HTTP_CODE=$(echo "$HEALTH" | grep "HTTP_CODE" | cut -d':' -f2)
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Health check passed"
    echo "$HEALTH" | grep -v "HTTP_CODE" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
else
    echo "❌ Health check failed (HTTP $HTTP_CODE)"
    echo "$HEALTH"
fi
echo ""

# Test 3: Debug replenish status
echo "[3/6] Testing /tasks/replenish/debug endpoint..."
DEBUG=$(curl -s -w "\nHTTP_CODE:%{http_code}" "${BASE_URL}/tasks/replenish/debug?token=${TOKEN}" 2>&1)
HTTP_CODE=$(echo "$DEBUG" | grep "HTTP_CODE" | cut -d':' -f2)
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Debug endpoint accessible"
    echo "$DEBUG" | grep -v "HTTP_CODE" | python3 -m json.tool 2>/dev/null || echo "$DEBUG"
else
    echo "❌ Debug endpoint failed (HTTP $HTTP_CODE)"
    echo "$DEBUG"
fi
echo ""

# Test 4: Summary endpoint
echo "[4/6] Testing /tasks/summary endpoint..."
SUMMARY=$(curl -s -w "\nHTTP_CODE:%{http_code}" "${BASE_URL}/tasks/summary?token=${TOKEN}" 2>&1)
HTTP_CODE=$(echo "$SUMMARY" | grep "HTTP_CODE" | cut -d':' -f2)
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Summary endpoint passed"
    echo "$SUMMARY" | grep -v "HTTP_CODE" | python3 -m json.tool 2>/dev/null || echo "$SUMMARY"
else
    echo "❌ Summary endpoint failed (HTTP $HTTP_CODE)"
    echo "$SUMMARY"
fi
echo ""

# Test 5: Replenish endpoint
echo "[5/6] Testing /tasks/replenish endpoint..."
REPLENISH=$(curl -s -w "\nHTTP_CODE:%{http_code}" "${BASE_URL}/tasks/replenish?token=${TOKEN}" 2>&1)
HTTP_CODE=$(echo "$REPLENISH" | grep "HTTP_CODE" | cut -d':' -f2)
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Replenish endpoint passed"
    RESULT=$(echo "$REPLENISH" | grep -v "HTTP_CODE" | python3 -m json.tool 2>/dev/null)
    echo "$RESULT"
    
    # Check if any items were replenished
    REPLENISHED=$(echo "$RESULT" | grep -o '"replenished":\s*\[.*\]' | grep -v '\[\]')
    if [ -n "$REPLENISHED" ]; then
        echo "✅ Items were replenished!"
    else
        echo "ℹ️  No items were replenished (may not be due yet)"
    fi
else
    echo "❌ Replenish endpoint failed (HTTP $HTTP_CODE)"
    echo "$REPLENISH"
fi
echo ""

# Test 6: Wrong token (should fail)
echo "[6/6] Testing authentication (wrong token should fail)..."
WRONG=$(curl -s -w "\nHTTP_CODE:%{http_code}" "${BASE_URL}/tasks/replenish?token=wrong" 2>&1)
HTTP_CODE=$(echo "$WRONG" | grep "HTTP_CODE" | cut -d':' -f2)
if [ "$HTTP_CODE" = "401" ]; then
    echo "✅ Authentication working correctly (rejected wrong token)"
else
    echo "⚠️  Unexpected response for wrong token (HTTP $HTTP_CODE)"
fi
echo ""

echo "========================================"
echo "Test Complete - $(date)"
echo "========================================"
