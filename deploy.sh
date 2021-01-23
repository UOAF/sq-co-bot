#!/bin/bash

systemctl --user stop sqcobot.service
echo "Going to sq-co-bot dir"
cd /opt/sq-co-bot/
echo "Fetching from Git and resetting to the latest master"
git fetch
git reset --hard origin/master
echo "Trying to start sqcobot service"
systemctl --user start sqcobot.service
