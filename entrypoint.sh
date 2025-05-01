#!/bin/bash
# start supervisor, which will start clamd + app
exec supervisord -c ./supervisord.conf
