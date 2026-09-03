#!/bin/bash
# Case 7's screen: a deploy that runs for a while and then fails.
#
# Long enough that polling in a tight loop is visibly wrong, and it ends in a
# state an action has to *read* rather than assume - plus a log on disk for
# "if it fails, grab the log".
set -u
log="${TMPDIR:-/tmp}/acme-deploy.log"
printf '\033]0;deploy - acme\007'
: > "$log"
say() { echo "$1"; echo "$1" >> "$log"; }
say "deploy acme 0.4.1 -> production"
for step in "pushing image" "waiting for capacity" "draining old pods" \
            "starting 3 replicas" "health checks" "switching traffic"; do
    say "[$(date +%H:%M:%S)] $step ..."
    sleep 9
done
say "[$(date +%H:%M:%S)] ERROR: 2 of 3 replicas failed their health check"
say "DEPLOY FAILED"
say "log: $log"
echo ""
echo "(this window is the screen; leave it open)"
exec bash -i
