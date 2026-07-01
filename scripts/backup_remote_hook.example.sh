#!/usr/bin/env bash
set -euo pipefail

# Example interface for off-machine backups.
#
# Do not put real tokens in this file. Copy it to a private ignored path, then
# configure BGH_BACKUP_AFTER_HOOK=/absolute/path/to/your/private-hook.sh.
#
# The backup runner calls this script with one argument: the local dump path.
# A real implementation can upload to rclone, iCloud Drive, S3, R2, OSS, etc.

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 backups/postgres/babygrow-YYYYMMDDTHHMMSSZ.dump" >&2
  exit 2
fi

backup_file="$1"

if [ ! -f "$backup_file" ]; then
  echo "Backup file not found: $backup_file" >&2
  exit 2
fi

echo "Remote backup hook placeholder received: $backup_file"
echo "Implement upload here when cloud drive or object storage is selected."
