@echo off
rem Run any Mask mode from any folder: mask.cmd skill start, mask.cmd guard hook ...
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m mask %*
