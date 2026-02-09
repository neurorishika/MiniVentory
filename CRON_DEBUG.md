# Debugging Cron Jobs for Auto-Replenish

## Quick Start - Run These Commands on Your Synology

### 1. Check Docker Container
```bash
# SSH into your Synology
ssh admin@129.85.109.1

# Run diagnostic script
chmod +x /path/to/docker_diagnose.sh
./docker_diagnose.sh
```

### 2. Test Endpoints
```bash
# Make test script executable
chmod +x /path/to/test_cron.sh

# Run tests
./test_cron.sh
```

## Manual Test Commands

### From Synology Host (where Docker runs):
```bash
# Test health
curl -v http://127.0.0.1:2152/health

# Test replenish debug (see what's configured)
curl -s "http://127.0.0.1:2152/tasks/replenish/debug?token=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6" | python3 -m json.tool

# Test summary endpoint
curl -s "http://127.0.0.1:2152/tasks/summary?token=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6"

# Test replenish endpoint
curl -s "http://127.0.0.1:2152/tasks/replenish?token=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6"
```

## Common Issues & Fixes

### Issue 1: "Connection Refused"
**Cause:** Docker container not running or port not exposed

**Fix:**
```bash
# Check if container is running
docker ps | grep 2152

# If not running, start it
docker-compose up -d

# Check logs for errors
docker-compose logs -f
```

### Issue 2: Auto-replenish not triggering
**Possible causes:**
1. **Wrong hour**: Auto-replenish only runs during the specific UTC hour you configured
2. **Next due date not reached**: Check with debug endpoint
3. **Not enabled**: Make sure auto_replenish_enabled=true for the item

**Debug:**
```bash
# Check current UTC time
date -u

# Check what items are configured and when they're due
curl -s "http://127.0.0.1:2152/tasks/replenish/debug?token=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6" \
  | python3 -m json.tool
```

**What to look for in debug output:**
- `current_hour_utc`: Current UTC hour
- `auto_replenish_hour_utc`: Hour when replenishment should trigger (must match!)
- `auto_replenish_next_due`: Next scheduled replenishment time
- `is_due_now`: Should be `true` if replenishment will happen now

### Issue 3: Cron job runs but nothing happens
**Cause:** Item configuration issue

**Fix in Admin Panel:**
1. Go to http://129.85.109.1:2152/admin/items
2. For each item you want auto-replenished:
   - ✅ Check "Enable Auto-Replenish"
   - Set "Replenish Quantity" (e.g., 100)
   - Set "Interval Type" (days/weeks/months)
   - Set "Interval Value" (e.g., 7 for weekly)
   - Set "Hour (UTC)" - this is critical! (e.g., 9 for 9:00 AM UTC)
   - Click "Update Auto-Replenish"

## Correct Cron Configuration for Synology Task Scheduler

### Summary Email (Hourly)
```bash
#!/bin/bash
curl -fsS "http://127.0.0.1:2152/tasks/summary?token=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6" >/dev/null 2>&1
```

### Auto-Replenish (Hourly)
```bash
#!/bin/bash
curl -fsS "http://127.0.0.1:2152/tasks/replenish?token=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6" >/dev/null 2>&1
```

**Important:** Schedule both to run **every hour** (the app handles the logic of when to actually send/replenish)

## Testing Auto-Replenish

### Force a test by setting hour to current hour:
1. Check current UTC hour: `date -u +'%H'` (e.g., returns 14)
2. In admin panel, set an item's "Hour (UTC)" to that hour
3. Set "Interval" to 1 day and save
4. **Wait 1-2 minutes** for next cron run OR manually trigger:
   ```bash
   curl -v "http://127.0.0.1:2152/tasks/replenish?token=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6"
   ```
5. Check admin logs to see if "SYSTEM" added stock

## Monitoring

### View recent logs in admin panel:
- http://129.85.109.1:2152/admin/logs
- Filter by user: "SYSTEM" to see auto-replenishments

### Check email delivery:
- Low stock alerts go to ADMIN_EMAIL
- Auto-replenish notifications go to ADMIN_EMAIL
- Check your spam folder!

## Environment Variables Checklist

Make sure your `.env` file has:
```bash
CRON_TOKEN=20bcfdfbf4f0520488895aeafcbeafe70e42374420d6b5a2212651d2670c62a6
ADMIN_EMAIL=your.email@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your.email@example.com
SMTP_PASSWORD=your_app_password
```

After changing `.env`, restart the container:
```bash
docker-compose down
docker-compose up -d
```
