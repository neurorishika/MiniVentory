#!/bin/bash
# Docker container diagnostic script for MiniVentory

echo "========================================"
echo "Docker Container Diagnostics"
echo "========================================"
echo ""

# Find container name/ID
echo "[1/8] Finding MiniVentory container..."
CONTAINER=$(docker ps -a --filter "expose=2152" --format "{{.ID}}\t{{.Names}}\t{{.Status}}" | head -1)
if [ -z "$CONTAINER" ]; then
    echo "❌ No container found exposing port 2152"
    echo "   → Try: docker-compose up -d"
    exit 1
fi

CONTAINER_ID=$(echo "$CONTAINER" | awk '{print $1}')
CONTAINER_NAME=$(echo "$CONTAINER" | awk '{print $2}')
CONTAINER_STATUS=$(echo "$CONTAINER" | cut -d$'\t' -f3-)

echo "✅ Found container: $CONTAINER_NAME ($CONTAINER_ID)"
echo "   Status: $CONTAINER_STATUS"
echo ""

# Check if running
echo "[2/8] Container state..."
if echo "$CONTAINER_STATUS" | grep -q "Up"; then
    echo "✅ Container is running"
else
    echo "❌ Container is NOT running"
    echo "   → Try: docker start $CONTAINER_NAME"
    echo "   → Or: docker-compose up -d"
fi
echo ""

# Check container logs
echo "[3/8] Recent container logs (last 20 lines)..."
echo "----------------------------------------"
docker logs $CONTAINER_ID --tail 20
echo "----------------------------------------"
echo ""

# Check port binding
echo "[4/8] Port bindings..."
docker port $CONTAINER_ID 2152
if [ $? -eq 0 ]; then
    echo "✅ Port 2152 is mapped"
else
    echo "❌ Port 2152 is NOT mapped"
fi
echo ""

# Check if app is listening inside container
echo "[5/8] Checking if app is listening inside container..."
docker exec $CONTAINER_ID netstat -tlnp 2>/dev/null | grep 2152
if [ $? -eq 0 ]; then
    echo "✅ App is listening on port 2152 inside container"
else
    echo "❌ App is NOT listening inside container"
    echo "   → Check logs for errors"
fi
echo ""

# Test health endpoint from inside container
echo "[6/8] Testing /health from inside container..."
HEALTH=$(docker exec $CONTAINER_ID curl -fsS http://127.0.0.1:2152/health 2>&1)
if [ $? -eq 0 ]; then
    echo "✅ Health endpoint works inside container"
    echo "$HEALTH"
else
    echo "❌ Health endpoint failed inside container"
    echo "$HEALTH"
fi
echo ""

# Check environment variables
echo "[7/8] Checking critical environment variables..."
docker exec $CONTAINER_ID printenv | grep -E "MONGO_URI|CRON_TOKEN|APP_PORT" || echo "⚠️  Some env vars may be missing"
echo ""

# Check MongoDB connection
echo "[8/8] Testing MongoDB connectivity..."
MONGO_TEST=$(docker exec $CONTAINER_ID python3 -c "
from pymongo import MongoClient
import os
try:
    client = MongoClient(os.environ.get('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=3000)
    client.server_info()
    print('✅ MongoDB connection successful')
except Exception as e:
    print(f'❌ MongoDB connection failed: {e}')
" 2>&1)
echo "$MONGO_TEST"
echo ""

echo "========================================"
echo "Diagnostic Complete"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. If container is down: docker-compose up -d"
echo "2. If app won't start: Check logs above for errors"
echo "3. If MongoDB fails: Check MONGO_URI in .env"
echo "4. If port not mapped: Check docker-compose.yml"
