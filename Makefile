# This repo uses Taskfile (https://taskfile.dev), not Make.
# Install:  brew install go-task
# See available tasks:  task
#
# This stub only exists so `make <x>` produces a helpful error instead of
# running something stale. Delete once everyone's migrated.

.DEFAULT_GOAL := notice

notice:
	@echo "This repo uses Taskfile, not Make."
	@echo "  brew install go-task"
	@echo "  task             # list available tasks"
	@echo "  task dev         # start the dev loop"
	@exit 1
