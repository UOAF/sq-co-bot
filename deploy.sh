#!/bin/bash

set -euxo pipefail

systemctl --user stop sqcobot.service || true
cd /opt/sq-co-bot/
git fetch
git reset --hard origin/master
systemctl --user restart sqcobot.service
