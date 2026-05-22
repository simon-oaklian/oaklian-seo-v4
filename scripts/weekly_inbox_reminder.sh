#!/bin/bash
# Weekly Monday 9 AM inbox reminder.
# Inserts an unread row into system_notifications.
# Sprint 2d / 6.1.
set -e
LOG_FILE="/home/simon/AI-SEO-oaklian/logs/weekly_reminder.log"
mkdir -p "$(dirname "$LOG_FILE")"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
docker exec -i OAKLIAN-SEO-DB psql -U seo_user -d seo << 'SQLEOF' >> "$LOG_FILE" 2>&1
INSERT INTO system_notifications (kind, title, body, target_url, status)
VALUES (
  'weekly_inbox',
  '📊 周一晨报',
  '上周流量数据已更新',
  '/analytics#oaklian',
  'unread'
);
SQLEOF
echo "[$TIMESTAMP] Inserted weekly reminder" >> "$LOG_FILE"
