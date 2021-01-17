#!/bin/bash

systemctl --user stop sqcobot.service
echo "Going go sq-co-bot dir"
cd /opt/sq-co-bot/
echo "Git pull in working dir"
git pull
echo "Trying to start sqcobot service"
systemctl --user start sqcobot.service