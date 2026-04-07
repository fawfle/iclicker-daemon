# iClicker Daemon
A simple background process that automates IClicker.

# Instructions
## Get an Access Token
Go to student.iclicker.com and log in. Then run `sessionStorage.access_token` in the console. Copy that value into the 'settings.json' file. The auth token will expire, so remember to update it.

## Run the Script
Run the python script with `uv run iclicker_daemon.py` within the project directory.


#### TODO
- add error message for current failing to update answer
- fix issue with failing to update answer
- v1 attendance api support
