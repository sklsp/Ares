# Changelog

## Unreleased
- Fix: completed, requeued and dead-lettered jobs are removed from the Redis processing list (the worker tag made
  the stored value unmatchable)
- Fix: a requeued job keeps its retry count, so a failing job reaches dead letter after 3 attempts instead of
  retrying forever
- Fix: reclaim no longer runs a dead-lettered job again

## v1.0.0
- First public release
