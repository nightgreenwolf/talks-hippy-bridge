# talks-bridge

Bridges a Matrix homeserver with `maubot` to bots developed with the [Talks Hippy](https://gitlab.com/gllona/talks-hippy) framework

## Features

- Bridges your Matrix server to bots developed with Talks Hippy
- Multiple Talks Hippy bots supported with a single `maubot` instance
- Support Matrix, WhatsApp, Signal and Telegram by using `mautrix` bridges
- Externalized configuration, no need to change the code
- Uses `asyncio` for efficient concurrency
- Operator can turn the bridge on and off per room. Use Element and log in as the Matrix bot user

## Prerequisites

Server:

1. A Matrix homeserver. You can deploy one using the Ansible playbook from https://github.com/spantaleev/matrix-docker-ansible-deploy
2. `maubot` installed in your Matrix homeserver
3. Custom configuration telling Synapse to allow the bots send many messages per second. You can set this configuration in
   the Ansible playbook `vars.yml` file:
```
matrix_synapse_rc_message:
  per_second: 100
  burst_count: 500
```

Local machine:

1. Python 3 with `pip` and `venv`

## Usage

1. Clone the repo from https://github.com/nightgreenwolf/talks-hippy-bridge
2. `bin/build_dependencies.sh` (run once)
3. `bin/zip_plugin.sh` (run for every deploy)
4. Upload `plugin.mbp` to maubot

## Configuration

The custom configuration is in `base-config.yaml`. You can specify a custom configuration in the `maubot` web admin tool.

Generally you will need to set only custom values for `matrix_bot_user`, `talks_server`, `talks_port`, `bot_on_regex` and `bot_off_regex`.

- `matrix_bot_user`: The Matrix user associated with the bot
- `talks_server`: The IP address or server name where the Talks Hippy bot is running
- `talks_port`: The HTTP port where the Talks Hippy bot is running
- `talks_protocol`: Tested with `http`
- `talks_receive_message`: Talks endpoint path to send a message to Talks, default `/matrix/receiveMessage`
- `talks_get_messages`: Talks endpoint path to query for messages to send back to users, default `/matrix/getMessages`
- `talks_confirm_messages`: Talks endpoint path to confirm messages sent to users, default `/matrix/confirmMessages`
- `bot_on_regex`: If a message sent by the bot user matches this regex, the bridge is activated for the room 
- `bot_off_regex`: If a message sent by the bot user matches this regex, the bridge is deactivated for the room
- `hints`: if `true`, message actions sent by Talks Hippy will be displayed as numeric shortcuts
- `deduplication_cache_size`: maximum size of the message deduplication cache
- `echo_cache_size`: maximum size of the cache that filters out echo messages
- `fixed_timeout`: Talks Hippy HTTP calls timeout in seconds
- `message_fetcher_delay`: The delay for each cycle of the message fetcher task, in seconds
- `message_propagator_delay`: The delay between two consecutive messages to the same room, in seconds
- `hints_delay`: The delay between the last message and the hints message, in seconds 

## Author

Copyright (C) 2023-2024  Night Green Wolf  <mailto:nightgreenwolf@protonmail.com>
