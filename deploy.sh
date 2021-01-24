#!/bin/bash

echo "::::::::::: stopping sqcobot.service"
systemctl --user stop sqcobot.service
echo "::::::::::: cd to sq-co-bot dir"
cd /opt/sq-co-bot/
echo "::::::::::: Git fetch in working dir"
git fetch
echo "::::::::::: Git reset in working dir"
git reset --hard origin/master
echo "::::::::::: starting sqcobot.service"
systemctl --user start sqcobot.service
echo "::::::::::: giving execution and server permissions back to deploy.sh"
chmod u+x deploy.sh